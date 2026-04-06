"""
Translation engine — menerjemahkan teks subtitle ke bahasa lain.

Mendukung:
  - Jepang → Indonesia
  - Jepang → Inggris
  - Inggris → Indonesia
  - Dan 100+ kombinasi bahasa lainnya
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from app.core.logging_config import get_logger
from app.models.schemas import TranscriptionResult, TranscriptionSegment

logger = get_logger(__name__)

# ── Daftar kode bahasa yang sering dipakai ───────────────────
LANGUAGE_NAMES = {
    "ja": "Jepang (Japanese)",
    "id": "Indonesia",
    "en": "Inggris (English)",
    "zh": "Mandarin (Chinese)",
    "ko": "Korea (Korean)",
    "ms": "Melayu (Malay)",
    "th": "Thai",
    "vi": "Vietnam",
    "de": "Jerman (German)",
    "fr": "Prancis (French)",
    "es": "Spanyol (Spanish)",
    "pt": "Portugis (Portuguese)",
    "ar": "Arab (Arabic)",
    "hi": "Hindi",
    "ru": "Rusia (Russian)",
    "it": "Italia (Italian)",
    "nl": "Belanda (Dutch)",
    "tr": "Turki (Turkish)",
    "pl": "Polandia (Polish)",
    "sv": "Swedia (Swedish)",
}


class TranslationEngine:
    """Menerjemahkan teks subtitle dari satu bahasa ke bahasa lain."""

    def __init__(
        self,
        source_lang: str = "ja",
        target_lang: str = "id",
        batch_size: int = 20,
        delay_between_batches: float = 0.5,
    ):
        """
        Args:
            source_lang: Kode bahasa sumber (ja, en, zh, dll.)
            target_lang: Kode bahasa tujuan (id, en, ms, dll.)
            batch_size: Berapa baris diterjemahkan sekaligus
            delay_between_batches: Jeda antar batch (detik), supaya tidak di-block
        """
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.batch_size = batch_size
        self.delay = delay_between_batches
        self._translator: Any = None

        self._setup_translator()

    def _setup_translator(self) -> None:
        """Inisialisasi Google Translator."""
        try:
            from deep_translator import GoogleTranslator

            # Mapping kode bahasa Whisper → Google Translate
            source = self._map_lang_code(self.source_lang)
            target = self._map_lang_code(self.target_lang)

            self._translator = GoogleTranslator(source=source, target=target)

            logger.info(
                "Translator ready: %s (%s) → %s (%s)",
                self.source_lang,
                LANGUAGE_NAMES.get(self.source_lang, "?"),
                self.target_lang,
                LANGUAGE_NAMES.get(self.target_lang, "?"),
            )
        except ImportError:
            raise ImportError(
                "Install deep-translator: pip install deep-translator"
            )
        except Exception as exc:
            logger.error("Failed to setup translator: %s", exc)
            raise

    # ── API Utama ────────────────────────────────────────────

    def translate_text(self, text: str) -> str:
        """Terjemahkan satu teks."""
        if not text or not text.strip():
            return text

        translator = self._translator
        if translator is None:
            return text

        try:
            result = translator.translate(text.strip())
            return result if result else text
        except Exception as exc:
            logger.warning("Translation failed for '%s...': %s", text[:30], exc)
            return text  # Kembalikan teks asli jika gagal

    def translate_segments(
        self,
        segments: List[TranscriptionSegment],
        progress_callback=None,
    ) -> List[TranscriptionSegment]:
        """
        Terjemahkan semua segment subtitle.
        Menggunakan batch processing supaya lebih cepat.
        """
        if not segments:
            return segments

        total = len(segments)
        translated: List[TranscriptionSegment] = []

        logger.info(
            "Translating %d segments: %s → %s",
            total, self.source_lang, self.target_lang,
        )

        # Proses dalam batch
        for batch_start in range(0, total, self.batch_size):
            batch_end = min(batch_start + self.batch_size, total)
            batch = segments[batch_start:batch_end]

            # Kumpulkan teks untuk diterjemahkan sekaligus
            texts = [seg.text for seg in batch]

            try:
                # Terjemahkan batch sekaligus (lebih cepat)
                translated_texts = self._translate_batch(texts)
            except Exception as exc:
                logger.warning("Batch translation failed: %s", exc)
                # Fallback: terjemahkan satu per satu
                translated_texts = self._translate_individually(texts)

            # Buat segment baru dengan teks terjemahan
            for seg, new_text in zip(batch, translated_texts):
                new_seg = seg.model_copy(
                    update={"text": new_text}
                )
                translated.append(new_seg)

            # Progress callback
            if progress_callback:
                pct = (batch_end / total) * 100
                progress_callback(
                    pct,
                    f"Translating… ({batch_end}/{total} segments)",
                )

            # Jeda supaya tidak kena rate limit
            if batch_end < total:
                time.sleep(self.delay)

        logger.info("Translation complete: %d segments", len(translated))
        return translated

    def translate_result(
        self,
        result: TranscriptionResult,
        progress_callback=None,
    ) -> TranscriptionResult:
        """Terjemahkan seluruh TranscriptionResult."""
        translated_segments = self.translate_segments(
            result.segments,
            progress_callback=progress_callback,
        )
        return result.model_copy(
            update={"segments": translated_segments}
        )

    # ── Internal Methods ─────────────────────────────────────

    def _translate_batch(self, texts: List[str]) -> List[str]:
        """Terjemahkan sekumpulan teks sekaligus."""
        prepared: List[str] = []
        restore_map: List[tuple[int, str]] = []
        for idx, text in enumerate(texts):
            stripped = text.strip()
            if not stripped:
                continue
            restore_map.append((idx, text))
            prepared.append(stripped)

        if not prepared:
            return list(texts)

        results = list(texts)
        translator = self._translator
        if translator is None:
            return results

        batch_method = getattr(translator, "translate_batch", None)

        if callable(batch_method):
            try:
                translated_batch_raw = batch_method(prepared)
                if isinstance(translated_batch_raw, list):
                    translated_batch = [str(item) for item in translated_batch_raw]
                    if len(translated_batch) == len(prepared):
                        for (idx, original), translated in zip(restore_map, translated_batch):
                            results[idx] = translated if translated else original
                        return results
            except Exception as exc:
                logger.warning("Fast batch translation unavailable: %s", exc)

        for idx, original in restore_map:
            try:
                translated = translator.translate(original.strip())
                results[idx] = translated if translated else original
            except Exception:
                results[idx] = original
        return results

    def _translate_individually(self, texts: List[str]) -> List[str]:
        """Fallback: terjemahkan satu per satu."""
        results = []
        for text in texts:
            results.append(self.translate_text(text))
            time.sleep(0.1)  # Jeda kecil
        return results

    @staticmethod
    def _map_lang_code(code: str) -> str:
        """
        Map kode bahasa Whisper ke kode Google Translate.
        Kebanyakan sama, tapi ada beberapa yang berbeda.
        """
        mapping = {
            "ja": "ja",        # Japanese
            "id": "id",        # Indonesian
            "en": "en",        # English
            "zh": "zh-CN",     # Chinese (Simplified)
            "zh-tw": "zh-TW",  # Chinese (Traditional)
            "ko": "ko",        # Korean
            "ms": "ms",        # Malay
            "jw": "jw",        # Javanese
            "su": "su",        # Sundanese
        }
        return mapping.get(code, code)

    @staticmethod
    def get_supported_languages() -> dict[str, str]:
        """Kembalikan daftar bahasa yang didukung."""
        try:
            from deep_translator import GoogleTranslator

            supported: Any = GoogleTranslator().get_supported_languages(as_dict=True)
            if isinstance(supported, dict):
                return {str(k): str(v) for k, v in supported.items()}
            return dict(LANGUAGE_NAMES)
        except Exception:
            return dict(LANGUAGE_NAMES)


# ── Factory Function ─────────────────────────────────────────

def create_translator(
    source: str = "ja",
    target: str = "id",
) -> TranslationEngine:
    """Buat translator baru."""
    return TranslationEngine(source_lang=source, target_lang=target)
