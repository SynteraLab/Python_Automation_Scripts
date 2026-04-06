"""
embed_services.py (v2) — Advanced Embed Service Detection System

Arsitektur baru:
  • EmbedServiceSignature v2 — structured per-service data
  • DomainEntry — domain dengan status classification
  • ExtractionProfile — deep extraction hints per service
  • LabelMapping — button label → service resolution
  • ServiceFamily — group related services
  • EmbedServiceRegistry — central registry + lookup engine
  • EmbedServiceDetector v2 — detection + label resolution

Desain prinsip:
  • Setiap service = 1 entry STRICT (tidak digabung)
  • Domain punya status (primary/common/mirror/legacy/dead)
  • Label mapping punya confidence + ambiguity flag
  • Extraction profile punya challenge_type + flow + failure_modes
  • Family/group untuk track relasi tanpa merge
"""

from __future__ import annotations

import re
import logging
from enum import Enum, auto
from typing import (
    ClassVar, Dict, FrozenSet, List, Optional, 
    Set, Tuple, Union
)
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  ENUMS
# ══════════════════════════════════════════════════════════

class DomainStatus(Enum):
    """Klasifikasi status domain"""
    PRIMARY = "primary"          # Domain utama, paling stabil
    COMMON = "common"            # Sering dipakai, aktif
    MIRROR = "mirror"            # Mirror/alternatif, aktif
    LEGACY = "legacy"            # Masih jalan tapi jarang dipakai
    DEAD = "dead"                # Mati / tidak aktif
    UNCERTAIN = "uncertain"      # Belum diverifikasi


class ExtractionMethod(Enum):
    """Metode utama extraction"""
    DIRECT = "direct"            # Direct MP4/video URL di page source
    HLS = "hls"                  # HLS m3u8 stream
    DASH = "dash"                # DASH mpd stream
    API = "api"                  # API endpoint return video URL
    EVAL_JS = "eval_js"          # Perlu eval obfuscated JavaScript
    IFRAME_CHAIN = "iframe_chain"  # Multi-layer iframe redirect
    EMBED_API = "embed_api"      # Embed-specific API (getSources, dll)
    WASM = "wasm"                # WebAssembly-based protection
    WEBSOCKET = "websocket"      # URL dikirim via WebSocket
    UNKNOWN = "unknown"


class ChallengeType(Enum):
    """Tipe tantangan/proteksi pada service"""
    NONE = "none"                            # Tidak ada proteksi khusus
    OBFUSCATED_JS = "obfuscated_js"          # JS obfuscation (eval, pack)
    WASM_DECRYPT = "wasm_decrypt"            # WebAssembly decryption
    TOKEN_ROTATION = "token_rotation"        # Token expire & rotate
    EVAL_CHAIN = "eval_chain"                # Chain of eval() calls
    ENCRYPTED_AJAX = "encrypted_ajax"        # Encrypted AJAX payload
    CAPTCHA = "captcha"                      # CAPTCHA challenge
    BROWSER_CHECK = "browser_check"          # Browser fingerprint check
    SIGNED_URL = "signed_url"               # Time-limited signed URLs
    ANTI_HOTLINK = "anti_hotlink"           # Referer/origin checking
    MULTI_REDIRECT = "multi_redirect"       # Multiple URL redirections
    PACKED_JS = "packed_js"                 # Dean Edwards packer
    BASE64_LAYERS = "base64_layers"         # Multiple base64 encode layers
    ROT_CIPHER = "rot_cipher"              # ROT13/custom rotation cipher
    CUSTOM_CRYPTO = "custom_crypto"         # Custom encryption algorithm
    RATE_LIMITED = "rate_limited"           # Rate limiting on extraction


class MediaFormat(Enum):
    """Format output media yang dihasilkan"""
    MP4 = "mp4"
    HLS = "hls"                  # m3u8 → ts segments
    DASH = "dash"                # mpd → m4s segments
    WEBM = "webm"
    MKV = "mkv"
    FLV = "flv"
    MIXED = "mixed"              # Bisa mp4 atau hls tergantung quality
    UNKNOWN = "unknown"


class ServiceTier(Enum):
    """Seberapa umum service ini ditemui"""
    TIER_1 = 1                   # Sangat umum, hampir semua site pakai
    TIER_2 = 2                   # Umum, sering ditemui
    TIER_3 = 3                   # Kadang ditemui
    TIER_4 = 4                   # Jarang / niche


class ServiceReliability(Enum):
    """Reliability service untuk extraction"""
    HIGH = "high"                # Stabil, jarang berubah
    MEDIUM = "medium"            # Kadang berubah tapi predictable
    LOW = "low"                  # Sering berubah, perlu maintenance
    UNSTABLE = "unstable"        # Sangat tidak stabil / sering mati


class FamilyRelation(Enum):
    """Relasi service dalam satu family"""
    PARENT = "parent"            # Service utama/original
    SIBLING = "sibling"          # Service terpisah tapi terkait
    ALIAS = "alias"              # Nama lain / rebrand dari parent
    REBRAND = "rebrand"          # Parent berubah nama
    FORK = "fork"                # Fork dari parent
    CHILD = "child"              # Sub-service dari parent


# ══════════════════════════════════════════════════════════
#  DOMAIN ENTRY
# ══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DomainEntry:
    """
    Satu domain dengan status classification.
    
    Contoh:
        DomainEntry("filemoon.sx", DomainStatus.PRIMARY)
        DomainEntry("filemoon.to", DomainStatus.COMMON)
        DomainEntry("filemoon.link", DomainStatus.MIRROR)
        DomainEntry("filemoon.cc", DomainStatus.DEAD)
    """
    domain: str
    status: DomainStatus = DomainStatus.COMMON
    note: str = ""

    def is_active(self) -> bool:
        """Apakah domain masih aktif?"""
        return self.status not in (DomainStatus.DEAD, DomainStatus.UNCERTAIN)

    def __str__(self) -> str:
        return f"{self.domain} [{self.status.value}]"


# ══════════════════════════════════════════════════════════
#  API ENDPOINT
# ══════════════════════════════════════════════════════════

@dataclass
class APIEndpoint:
    """
    Satu API endpoint yang dipakai service.
    Service bisa punya beberapa endpoint (getSources, getToken, dll)
    """
    path: str                        # "/ajax/embed-6/getSources"
    method: str = "GET"              # GET, POST
    purpose: str = ""                # "get_sources", "get_token", "verify"
    requires_id: bool = True         # Apakah perlu video ID di URL
    id_param: str = "id"             # Nama parameter untuk video ID
    
    # Request requirements
    content_type: str = ""           # Request content-type
    required_headers: Dict[str, str] = field(default_factory=dict)
    required_params: Dict[str, str] = field(default_factory=dict)
    
    # Response info
    response_format: str = "json"    # json, text, html, binary
    media_key_paths: List[str] = field(default_factory=list)
    # contoh: ["sources[0].file", "data.source", "videoSource"]
    
    note: str = ""


# ══════════════════════════════════════════════════════════
#  EXTRACTION PROFILE
# ══════════════════════════════════════════════════════════

@dataclass
class ExtractionProfile:
    """
    Profil extraction mendalam untuk satu service.
    Ini menjawab: "Bagaimana cara extract video dari service ini?"
    """
    # ── Primary Method ──
    method: ExtractionMethod = ExtractionMethod.UNKNOWN
    typical_format: MediaFormat = MediaFormat.UNKNOWN
    
    # ── Challenge / Protection ──
    challenge_types: List[ChallengeType] = field(
        default_factory=lambda: [ChallengeType.NONE]
    )
    
    # ── Step-by-step Flow ──
    # Human-readable extraction flow
    flow_steps: List[str] = field(default_factory=list)
    # Contoh:
    # ["1. Fetch embed page HTML",
    #  "2. Find packed/obfuscated JS block",
    #  "3. Unpack with P.A.C.K.E.R unpacker",
    #  "4. Extract m3u8 URL from unpacked JS",
    #  "5. Fetch m3u8 with Referer header"]
    
    # ── Pseudo-code Flow ──
    # Lebih teknis, bisa dijadikan panduan coding
    pseudo_flow: List[str] = field(default_factory=list)
    # Contoh:
    # ["html = fetch(embed_url, referer=page_url)",
    #  "packed = regex(r'eval\\(function\\(p,a,c,k,e,d\\).+?\\)', html)",
    #  "unpacked = js_unpack(packed)",
    #  "m3u8_url = regex(r'file:\"([^\"]+\\.m3u8[^\"]*)', unpacked)",
    #  "video = fetch_hls(m3u8_url, referer=embed_url)"]
    
    # ── Implementable Hints ──
    # Regex patterns, decode methods, dll
    key_regex_patterns: Dict[str, str] = field(default_factory=dict)
    # Contoh: {"packed_js": r'eval\(function\(p,a,c,k,e,d\).*?\)\)',
    #          "m3u8_url": r'file:\s*"([^"]+\.m3u8[^"]*)"'}
    
    decode_methods: List[str] = field(default_factory=list)
    # Contoh: ["base64", "rot13", "reverse", "js_unpack_p_a_c_k_e_d"]
    
    # ── Dependencies ──
    requires_referer: bool = True
    requires_origin: bool = False
    requires_cookies: bool = False
    requires_user_agent: bool = False
    
    header_dependencies: Dict[str, str] = field(default_factory=dict)
    # Headers yang HARUS dikirim. Value bisa berupa template.
    # Contoh: {"Referer": "{embed_url}", "X-Requested-With": "XMLHttpRequest"}
    
    cookie_dependencies: List[str] = field(default_factory=list)
    # Cookie names yang harus ada
    # Contoh: ["session_id", "cf_clearance"]
    
    # ── API Endpoints ──
    api_endpoints: List[APIEndpoint] = field(default_factory=list)
    
    # ── Timing ──
    requires_delay: bool = False
    min_delay_seconds: float = 0.0     # Delay sebelum fetch video
    token_expiry_seconds: int = 0      # 0 = tidak expire
    
    # ── Fallback ──
    fallback_strategy: str = ""
    # Contoh: "Jika API gagal, fallback ke regex di HTML source"
    
    # ── Known Failure Modes ──
    failure_modes: List[str] = field(default_factory=list)
    # Contoh: ["Token expired setelah 4 jam",
    #          "403 jika Referer salah",
    #          "Domain sering ganti, perlu update",
    #          "Rate limit 20 req/menit"]
    
    # ── Browser Requirement ──
    requires_browser: bool = False
    browser_note: str = ""
    # Contoh: "Butuh browser untuk execute JS eval chain"


# ══════════════════════════════════════════════════════════
#  LABEL MAPPING
# ══════════════════════════════════════════════════════════

@dataclass
class LabelMapping:
    """
    Mapping dari button label (contoh: "ST") ke service.
    
    Satu label bisa map ke beberapa service (ambiguous),
    masing-masing dengan confidence score.
    """
    label: str                       # "ST", "FM", "DD", dll
    service_id: str                  # "streamtape"
    confidence: float = 0.9          # 0.0 - 1.0
    
    # Ambiguity handling
    ambiguous: bool = False
    alternatives: List[str] = field(default_factory=list)
    # Service IDs lain yang mungkin untuk label ini
    
    # Context helps
    context_hints: List[str] = field(default_factory=list)
    # Hint untuk disambiguate: 
    # ["check iframe URL for streamtape domain",
    #  "usually appears in server list position 2-3"]
    
    example_sites: List[str] = field(default_factory=list)
    # Site yang diketahui pakai label ini
    
    note: str = ""


@dataclass
class LabelVariant:
    """
    Kumpulan label variants untuk satu service.
    Satu service bisa punya banyak label.
    """
    service_id: str
    labels: List[str] = field(default_factory=list)
    # Contoh untuk streamtape: ["ST", "Streamtape", "STP", "Tape"]
    
    # Case-insensitive matching
    case_sensitive: bool = False
    
    # Partial match support
    # Jika True, "StreamTape Server" cocok dengan label "Streamtape"
    partial_match: bool = True


# ══════════════════════════════════════════════════════════
#  SERVICE FAMILY
# ══════════════════════════════════════════════════════════

@dataclass
class FamilyMember:
    """Satu member dalam service family"""
    service_id: str
    relation: FamilyRelation
    note: str = ""


@dataclass
class ServiceFamily:
    """
    Group service yang terkait.
    Mereka TIDAK digabung — tetap entry terpisah.
    Family hanya untuk informasi relasi.
    """
    family_id: str                   # "filemoon_family"
    display_name: str                # "FileMoon Family"
    members: List[FamilyMember] = field(default_factory=list)
    
    # Shared characteristics
    shared_extraction_method: Optional[ExtractionMethod] = None
    shared_api_pattern: str = ""     # Jika semua member pakai API serupa
    
    note: str = ""
    
    def get_parent(self) -> Optional[str]:
        """Return service_id dari parent member"""
        for m in self.members:
            if m.relation == FamilyRelation.PARENT:
                return m.service_id
        return None
    
    def get_member_ids(self) -> List[str]:
        """Return semua service_ids dalam family"""
        return [m.service_id for m in self.members]


# ══════════════════════════════════════════════════════════
#  EMBED SERVICE SIGNATURE v2
# ══════════════════════════════════════════════════════════

@dataclass
class EmbedServiceSignature:
    """
    Signature lengkap satu embed/hosting service.
    Ini adalah data class UTAMA — satu instance per service.
    
    Prinsip:
    - 1 service = 1 entry (TIDAK digabung)
    - Domain punya status classification
    - Extraction punya challenge type + flow + failure modes
    - Label mapping terpisah tapi linked via service_id
    """
    
    # ── Identity ──
    id: str                          # Canonical ID: "streamtape", "filemoon"
    display_name: str                # Human-readable: "StreamTape", "FileMoon"
    
    family_id: str = ""              # Family group: "filemoon_family", ""
    family_relation: FamilyRelation = FamilyRelation.PARENT
    
    aliases: List[str] = field(default_factory=list)
    # Nama alternatif: ["strtape", "stape", "tape"]
    
    # ── Domains (classified) ──
    domains: List[DomainEntry] = field(default_factory=list)
    
    # ── Detection Patterns ──
    url_patterns: List[str] = field(default_factory=list)
    # Regex patterns untuk URL matching
    
    iframe_indicators: List[str] = field(default_factory=list)
    # Strings yang muncul di iframe src
    
    html_indicators: List[str] = field(default_factory=list)
    # Strings yang muncul di HTML halaman embed
    
    js_indicators: List[str] = field(default_factory=list)
    # Strings yang muncul di JavaScript
    
    # ── Extraction Profile ──
    extraction: ExtractionProfile = field(
        default_factory=ExtractionProfile
    )
    
    # ── Metadata ──
    tier: ServiceTier = ServiceTier.TIER_2
    reliability: ServiceReliability = ServiceReliability.MEDIUM
    last_verified: str = ""          # ISO date: "2024-01-15"
    notes: str = ""
    
    # ══════════════════════════════
    #  COMPUTED PROPERTIES
    # ══════════════════════════════
    
    @property
    def active_domains(self) -> List[str]:
        """Return hanya domain yang aktif"""
        return [d.domain for d in self.domains if d.is_active()]
    
    @property
    def primary_domain(self) -> Optional[str]:
        """Return primary domain, atau first active"""
        for d in self.domains:
            if d.status == DomainStatus.PRIMARY:
                return d.domain
        # Fallback: first active
        active = self.active_domains
        return active[0] if active else None
    
    @property
    def all_domains(self) -> List[str]:
        """Return semua domain (termasuk dead)"""
        return [d.domain for d in self.domains]
    
    @property
    def challenge_level(self) -> str:
        """
        Estimate difficulty level berdasarkan challenges.
        Return: 'easy', 'medium', 'hard', 'very_hard'
        """
        challenges = self.extraction.challenge_types
        
        if challenges == [ChallengeType.NONE]:
            return 'easy'
        
        hard_challenges = {
            ChallengeType.WASM_DECRYPT,
            ChallengeType.CUSTOM_CRYPTO,
            ChallengeType.CAPTCHA,
        }
        medium_challenges = {
            ChallengeType.OBFUSCATED_JS,
            ChallengeType.EVAL_CHAIN,
            ChallengeType.PACKED_JS,
            ChallengeType.ENCRYPTED_AJAX,
        }
        
        if any(c in hard_challenges for c in challenges):
            return 'very_hard'
        elif any(c in medium_challenges for c in challenges):
            return 'hard'
        elif len(challenges) > 1:
            return 'medium'
        else:
            return 'medium'
    
    def matches_url(self, url: str) -> bool:
        """Check apakah URL cocok dengan service ini"""
        url_lower = url.lower()
        
        # Check domains
        for domain_entry in self.domains:
            if domain_entry.domain in url_lower:
                return True
        
        # Check URL patterns
        for pattern in self.url_patterns:
            if re.search(pattern, url_lower):
                return True
        
        return False
    
    def matches_html(self, html: str) -> bool:
        """Check apakah HTML mengandung indicators service ini"""
        html_lower = html.lower()
        
        for indicator in self.html_indicators:
            if indicator.lower() in html_lower:
                return True
        
        for indicator in self.iframe_indicators:
            if indicator.lower() in html_lower:
                return True
        
        return False
    
    def get_extraction_summary(self) -> Dict:
        """Return summary extraction profile untuk report"""
        ext = self.extraction
        return {
            'service': self.display_name,
            'method': ext.method.value,
            'format': ext.typical_format.value,
            'challenges': [c.value for c in ext.challenge_types],
            'challenge_level': self.challenge_level,
            'requires_browser': ext.requires_browser,
            'requires_referer': ext.requires_referer,
            'requires_cookies': ext.requires_cookies,
            'api_endpoints': [
                {'path': ep.path, 'method': ep.method, 'purpose': ep.purpose}
                for ep in ext.api_endpoints
            ],
            'flow_steps': ext.flow_steps,
            'failure_modes': ext.failure_modes,
            'fallback': ext.fallback_strategy,
            'token_expiry': ext.token_expiry_seconds,
            'notes': self.notes,
        }


# ══════════════════════════════════════════════════════════
#  EMBED SERVICE REGISTRY
# ══════════════════════════════════════════════════════════

class EmbedServiceRegistry:
    """
    Central registry untuk semua embed services.
    Singleton pattern — satu instance untuk seluruh aplikasi.
    
    Responsibilities:
    - Store semua service signatures
    - Store semua label mappings
    - Store semua family definitions
    - Provide lookup methods (by URL, label, domain, ID)
    - Domain → service reverse lookup cache
    """
    
    _instance: ClassVar[Optional['EmbedServiceRegistry']] = None
    
    def __init__(self):
        self._services: Dict[str, EmbedServiceSignature] = {}
        self._labels: List[LabelMapping] = []
        self._label_variants: Dict[str, LabelVariant] = {}
        self._families: Dict[str, ServiceFamily] = {}
        
        # Caches (rebuilt on register)
        self._domain_cache: Dict[str, str] = {}     # domain → service_id
        self._pattern_cache: List[Tuple[re.Pattern, str]] = []  # compiled pattern → service_id
        self._label_cache: Dict[str, List[LabelMapping]] = {}   # label_upper → [mappings]
    
    @classmethod
    def get_instance(cls) -> 'EmbedServiceRegistry':
        """Singleton access"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset(cls):
        """Reset singleton (untuk testing)"""
        cls._instance = None
    
    # ══════════════════════════════
    #  REGISTRATION
    # ══════════════════════════════
    
    def register_service(self, service: EmbedServiceSignature):
        """Register satu service"""
        if service.id in self._services:
            logger.warning(
                f"Service '{service.id}' already registered, overwriting"
            )
        
        self._services[service.id] = service
        
        # Update domain cache
        for domain_entry in service.domains:
            self._domain_cache[domain_entry.domain.lower()] = service.id
        
        # Update pattern cache
        for pattern_str in service.url_patterns:
            try:
                compiled = re.compile(pattern_str, re.IGNORECASE)
                self._pattern_cache.append((compiled, service.id))
            except re.error as e:
                logger.error(
                    f"Invalid regex for {service.id}: {pattern_str} — {e}"
                )
        
        logger.debug(f"Registered service: {service.id} ({service.display_name})")
    
    def register_services(self, services: List[EmbedServiceSignature]):
        """Register batch services"""
        for svc in services:
            self.register_service(svc)
        logger.info(f"Registered {len(services)} services")
    
    def register_label(self, mapping: LabelMapping):
        """Register satu label mapping"""
        self._labels.append(mapping)
        
        # Update label cache
        key = mapping.label.upper().strip()
        if key not in self._label_cache:
            self._label_cache[key] = []
        self._label_cache[key].append(mapping)
    
    def register_labels(self, mappings: List[LabelMapping]):
        """Register batch labels"""
        for m in mappings:
            self.register_label(m)
        logger.info(f"Registered {len(mappings)} label mappings")
    
    def register_label_variants(self, variants: LabelVariant):
        """Register label variants untuk satu service"""
        self._label_variants[variants.service_id] = variants
        
        # Auto-create LabelMappings dari variants
        for label in variants.labels:
            existing = self._label_cache.get(label.upper().strip(), [])
            already = any(
                m.service_id == variants.service_id for m in existing
            )
            if not already:
                self.register_label(LabelMapping(
                    label=label,
                    service_id=variants.service_id,
                    confidence=0.85,
                ))
    
    def register_family(self, family: ServiceFamily):
        """Register service family"""
        self._families[family.family_id] = family
        logger.debug(
            f"Registered family: {family.family_id} "
            f"({len(family.members)} members)"
        )
    
    # ══════════════════════════════
    #  LOOKUP BY URL
    # ══════════════════════════════
    
    def identify_by_url(self, url: str) -> Optional[EmbedServiceSignature]:
        """
        Identify service dari URL.
        Check order: domain cache → URL patterns → brute force.
        """
        if not url:
            return None
        
        url_lower = url.lower()
        
        # 1. Domain cache (fastest)
        for domain, service_id in self._domain_cache.items():
            if domain in url_lower:
                return self._services.get(service_id)
        
        # 2. Compiled URL patterns
        for pattern, service_id in self._pattern_cache:
            if pattern.search(url_lower):
                return self._services.get(service_id)
        
        # 3. Brute force check (fallback)
        for service in self._services.values():
            if service.matches_url(url):
                return service
        
        return None
    
    def identify_name_by_url(self, url: str) -> str:
        """Return display_name dari URL, atau 'unknown'"""
        svc = self.identify_by_url(url)
        return svc.display_name if svc else 'unknown'
    
    def identify_id_by_url(self, url: str) -> str:
        """Return service id dari URL, atau 'unknown'"""
        svc = self.identify_by_url(url)
        return svc.id if svc else 'unknown'
    
    # ══════════════════════════════
    #  LOOKUP BY LABEL
    # ══════════════════════════════
    
    def resolve_label(self, label: str) -> List[LabelMapping]:
        """
        Resolve button label ke possible services.
        Return list sorted by confidence (highest first).
        
        Contoh:
            resolve_label("ST") → [LabelMapping(streamtape, 0.95)]
            resolve_label("FST") → [LabelMapping(fastream, 0.5), 
                                     LabelMapping(filestream, 0.4)]
        """
        if not label:
            return []
        
        key = label.upper().strip()
        
        # 1. Exact match di cache
        exact = self._label_cache.get(key, [])
        if exact:
            return sorted(exact, key=lambda m: m.confidence, reverse=True)
        
        # 2. Partial match — label yang lebih panjang
        #    Contoh: button text "Streamtape Server" → match "Streamtape"
        results = []
        label_lower = label.lower().strip()
        
        for mapping in self._labels:
            mapping_lower = mapping.label.lower()
            
            # Label ada di dalam text button
            if mapping_lower in label_lower:
                adjusted = LabelMapping(
                    label=label,
                    service_id=mapping.service_id,
                    confidence=mapping.confidence * 0.8,  # reduce for partial
                    ambiguous=mapping.ambiguous,
                    alternatives=mapping.alternatives,
                    context_hints=mapping.context_hints,
                    note=f"partial match: '{mapping.label}' in '{label}'",
                )
                results.append(adjusted)
            
            # Text button ada di dalam label 
            elif label_lower in mapping_lower:
                adjusted = LabelMapping(
                    label=label,
                    service_id=mapping.service_id,
                    confidence=mapping.confidence * 0.7,
                    ambiguous=True,
                    note=f"reverse partial: '{label}' in '{mapping.label}'",
                )
                results.append(adjusted)
        
        # 3. Check label variants
        for service_id, variants in self._label_variants.items():
            for variant_label in variants.labels:
                if variants.case_sensitive:
                    match = variant_label == label
                else:
                    match = variant_label.lower() == label_lower
                
                if match and service_id not in [r.service_id for r in results]:
                    results.append(LabelMapping(
                        label=label,
                        service_id=service_id,
                        confidence=0.85,
                        note="from label variants",
                    ))
                
                # Partial match for variants
                elif (variants.partial_match and 
                      variant_label.lower() in label_lower and
                      service_id not in [r.service_id for r in results]):
                    results.append(LabelMapping(
                        label=label,
                        service_id=service_id,
                        confidence=0.7,
                        note="partial from label variants",
                    ))
        
        # Deduplicate by service_id, keep highest confidence
        seen = {}
        for r in results:
            if r.service_id not in seen or r.confidence > seen[r.service_id].confidence:
                seen[r.service_id] = r
        
        return sorted(seen.values(), key=lambda m: m.confidence, reverse=True)
    
    def resolve_label_best(self, label: str) -> Optional[LabelMapping]:
        """Return single best match untuk label, atau None"""
        matches = self.resolve_label(label)
        return matches[0] if matches else None
    
    def resolve_label_service(self, label: str) -> Optional[EmbedServiceSignature]:
        """Return service signature dari label, atau None"""
        best = self.resolve_label_best(label)
        if best:
            return self._services.get(best.service_id)
        return None
    
    # ══════════════════════════════
    #  LOOKUP BY ID / NAME
    # ══════════════════════════════
    
    def get_service(self, service_id: str) -> Optional[EmbedServiceSignature]:
        """Get service by canonical ID"""
        return self._services.get(service_id)
    
    def get_family(self, family_id: str) -> Optional[ServiceFamily]:
        """Get family by family ID"""
        return self._families.get(family_id)
    
    def get_family_of(self, service_id: str) -> Optional[ServiceFamily]:
        """Get family yang service ini termasuk"""
        svc = self._services.get(service_id)
        if svc and svc.family_id:
            return self._families.get(svc.family_id)
        return None
    
    def get_related_services(self, service_id: str) -> List[EmbedServiceSignature]:
        """Get semua service dalam family yang sama"""
        family = self.get_family_of(service_id)
        if not family:
            return []
        
        related = []
        for member in family.members:
            if member.service_id != service_id:
                svc = self._services.get(member.service_id)
                if svc:
                    related.append(svc)
        return related
    
    # ══════════════════════════════
    #  SCAN & DETECT
    # ══════════════════════════════
    
    def scan_html(self, html: str) -> List[Dict]:
        """
        Scan HTML untuk semua embed services yang direferensikan.
        Return list of {service_id, display_name, domain_matched, ...}
        """
        found = []
        seen_ids: Set[str] = set()
        html_lower = html.lower()
        
        for service_id, service in self._services.items():
            if service_id in seen_ids:
                continue
            
            # Check domains
            for domain_entry in service.domains:
                if domain_entry.domain.lower() in html_lower:
                    seen_ids.add(service_id)
                    found.append({
                        'service_id': service_id,
                        'display_name': service.display_name,
                        'domain_matched': domain_entry.domain,
                        'domain_status': domain_entry.status.value,
                        'extraction_method': service.extraction.method.value,
                        'typical_format': service.extraction.typical_format.value,
                        'challenge_level': service.challenge_level,
                        'tier': service.tier.value,
                    })
                    break
            
            if service_id in seen_ids:
                continue
            
            # Check html_indicators
            for indicator in service.html_indicators:
                if indicator.lower() in html_lower:
                    seen_ids.add(service_id)
                    found.append({
                        'service_id': service_id,
                        'display_name': service.display_name,
                        'indicator_matched': indicator,
                        'extraction_method': service.extraction.method.value,
                        'typical_format': service.extraction.typical_format.value,
                        'challenge_level': service.challenge_level,
                        'tier': service.tier.value,
                    })
                    break
        
        return found
    
    def is_known_service(self, url: str) -> bool:
        """Check apakah URL dari embed service yang dikenal"""
        return self.identify_by_url(url) is not None
    
    def get_extraction_hints(self, url: str) -> Dict:
        """Get extraction hints lengkap untuk URL"""
        service = self.identify_by_url(url)
        if not service:
            return {
                'known': False,
                'service': 'unknown',
                'note': 'Service not recognized. Manual analysis needed.',
            }
        
        hints = service.get_extraction_summary()
        hints['known'] = True
        
        # Add family info
        family = self.get_family_of(service.id)
        if family:
            hints['family'] = family.family_id
            hints['related_services'] = [
                m.service_id for m in family.members 
                if m.service_id != service.id
            ]
        
        return hints
    
    # ══════════════════════════════
    #  QUERIES
    # ══════════════════════════════
    
    def list_all_services(self) -> List[EmbedServiceSignature]:
        """Return semua registered services"""
        return list(self._services.values())
    
    def list_services_by_tier(self, tier: ServiceTier) -> List[EmbedServiceSignature]:
        """Return services filtered by tier"""
        return [s for s in self._services.values() if s.tier == tier]
    
    def list_all_active_domains(self) -> Dict[str, str]:
        """Return semua active domains → service_id"""
        result = {}
        for service_id, service in self._services.items():
            for d in service.domains:
                if d.is_active():
                    result[d.domain] = service_id
        return result
    
    def list_all_families(self) -> List[ServiceFamily]:
        """Return semua families"""
        return list(self._families.values())
    
    def list_all_labels(self) -> List[LabelMapping]:
        """Return semua label mappings"""
        return self._labels.copy()
    
    def get_stats(self) -> Dict:
        """Return statistics"""
        return {
            'total_services': len(self._services),
            'total_domains': sum(
                len(s.domains) for s in self._services.values()
            ),
            'total_active_domains': len(self.list_all_active_domains()),
            'total_labels': len(self._labels),
            'total_families': len(self._families),
            'by_tier': {
                tier.name: len(self.list_services_by_tier(tier))
                for tier in ServiceTier
            },
            'by_method': {},
            'by_challenge': {},
        }
    
    def __repr__(self) -> str:
        stats = self.get_stats()
        return (
            f"EmbedServiceRegistry("
            f"services={stats['total_services']}, "
            f"domains={stats['total_domains']}, "
            f"labels={stats['total_labels']}, "
            f"families={stats['total_families']})"
        )


# ══════════════════════════════════════════════════════════
#  DETECTOR (High-Level API)
# ══════════════════════════════════════════════════════════

class EmbedServiceDetector:
    """
    High-level detection API.
    Wraps EmbedServiceRegistry dengan convenience methods.
    
    Ini adalah interface yang dipakai oleh layers dan
    server_switcher. Mereka tidak perlu tahu tentang
    Registry internals.
    
    Usage:
        detector = EmbedServiceDetector()
        
        # Identify dari URL
        service = detector.identify("https://filemoon.sx/e/abc123")
        print(service.display_name)  # "FileMoon"
        
        # Resolve button label
        result = detector.resolve_label("ST")
        print(result.service_id)  # "streamtape"
        
        # Get extraction hints
        hints = detector.get_hints("https://streamtape.com/e/xxx")
        print(hints['method'])  # "api"
        
        # Scan HTML
        found = detector.scan_html(page_html)
    """
    
    def __init__(self, registry: Optional[EmbedServiceRegistry] = None):
        self.registry = registry or EmbedServiceRegistry.get_instance()
    
    # ── URL-based ──
    
    def identify(self, url: str) -> Optional[EmbedServiceSignature]:
        """Identify service dari URL"""
        return self.registry.identify_by_url(url)
    
    def identify_name(self, url: str) -> str:
        """Return display name dari URL, atau 'unknown'"""
        return self.registry.identify_name_by_url(url)
    
    def identify_id(self, url: str) -> str:
        """Return service ID dari URL, atau 'unknown'"""
        return self.registry.identify_id_by_url(url)
    
    def is_known(self, url: str) -> bool:
        """Check apakah URL dari service yang dikenal"""
        return self.registry.is_known_service(url)
    
    def get_hints(self, url: str) -> Dict:
        """Get extraction hints dari URL"""
        return self.registry.get_extraction_hints(url)
    
    # ── Label-based ──
    
    def resolve_label(self, label: str) -> Optional[LabelMapping]:
        """Resolve label ke best match"""
        return self.registry.resolve_label_best(label)
    
    def resolve_label_all(self, label: str) -> List[LabelMapping]:
        """Resolve label ke semua possible matches"""
        return self.registry.resolve_label(label)
    
    def resolve_label_service(self, label: str) -> Optional[EmbedServiceSignature]:
        """Resolve label langsung ke service signature"""
        return self.registry.resolve_label_service(label)
    
    def resolve_label_with_url(
        self, label: str, iframe_url: str = ""
    ) -> Optional[EmbedServiceSignature]:
        """
        Resolve label dengan bantuan iframe URL untuk disambiguate.
        
        Strategy:
        1. Jika iframe URL dikenal → pakai itu (highest confidence)
        2. Jika label unambiguous → pakai label
        3. Jika label ambiguous + no URL → return highest confidence match
        """
        # 1. Try URL first
        if iframe_url:
            url_match = self.identify(iframe_url)
            if url_match:
                return url_match
        
        # 2. Try label
        return self.registry.resolve_label_service(label)
    
    # ── HTML-based ──
    
    def scan_html(self, html: str) -> List[Dict]:
        """Scan HTML untuk semua embed services"""
        return self.registry.scan_html(html)
    
    # ── Family-based ──
    
    def get_family(self, service_id: str) -> Optional[ServiceFamily]:
        """Get family yang service ini termasuk"""
        return self.registry.get_family_of(service_id)
    
    def get_related(self, service_id: str) -> List[EmbedServiceSignature]:
        """Get related services"""
        return self.registry.get_related_services(service_id)
    
    # ── Service access ──
    
    def get_service(self, service_id: str) -> Optional[EmbedServiceSignature]:
        """Get service by ID"""
        return self.registry.get_service(service_id)
    
    def list_all(self) -> List[EmbedServiceSignature]:
        """List semua services"""
        return self.registry.list_all_services()
    
    def stats(self) -> Dict:
        """Registry statistics"""
        return self.registry.get_stats()

    @classmethod
    def identify_service(cls, url: str) -> Optional[EmbedServiceSignature]:
        """Compatibility wrapper used by existing layers."""

        return get_detector().identify(url)

    @classmethod
    def identify_service_name(cls, url: str) -> str:
        """Compatibility wrapper returning a display name."""

        return get_detector().identify_name(url)

    @classmethod
    def scan_html_for_services(cls, html: str) -> List[Dict]:
        """Compatibility wrapper returning HTML scan results."""

        return get_detector().scan_html(html)

    @classmethod
    def is_embed_service(cls, url: str) -> bool:
        """Compatibility wrapper checking whether a URL is known."""

        return get_detector().is_known(url)

    @classmethod
    def get_extraction_hints(cls, url: str) -> Dict:
        """Compatibility wrapper returning extraction hints."""

        return get_detector().get_hints(url)


# ══════════════════════════════════════════════════════════
#  CONVENIENCE: SERVER SELECTOR CSS + DATA ATTRS
#  (Tetap di sini karena dipakai oleh server_switcher)
# ══════════════════════════════════════════════════════════

SERVER_SELECTOR_CSS = [
    # Generic server/source selectors
    '[data-server]',
    '[data-source]',
    '[data-embed]',
    '[data-id][class*="server"]',
    '[data-id][class*="source"]',
    '[data-video]',
    '[data-link-id]',
    '[data-episode-id][data-server-id]',
    '[data-src-id]',

    # Common class patterns
    '.server-item',
    '.server-list a',
    '.server-list li',
    '.server-select a',
    '.source-item',
    '.source-list a',
    '.episodes-servers a',
    '.player-server a',
    '.player-servers a',
    '.server_item',
    '.server_list a',
    '.choose-server a',
    '.servers a',
    '.server a',

    # Tab-based servers
    '.nav-server a',
    '.nav-servers a',
    '.tab-server',
    '[role="tab"][data-server]',
    '[role="tab"][data-embed]',

    # Dropdown-based
    'select.server-select option',
    'select[name="server"] option',
    '#server-select option',

    # Button-based
    'button[data-server]',
    'button[data-source]',
    'button.server-btn',

    # Site-specific common patterns
    '.anime-server a',
    '.vidcloud-servers a',
    '.embed-servers a',
    '#servers-container a',
    '#server-list a',
    '.watching_player-servers a',
    '.player_episodes-servers a',
    '.items-server a',
]

SERVER_DATA_ATTRIBUTES = [
    'data-server', 'data-source', 'data-embed', 'data-id',
    'data-video', 'data-link-id', 'data-server-id',
    'data-src-id', 'data-ep-id', 'data-episode-id',
    'data-hash', 'data-type', 'data-index', 'data-num',
    'data-name', 'data-provider', 'data-quality',
    'data-lang', 'data-sub', 'data-dub',
]

SERVER_API_PATTERNS = [
    r'/ajax/(?:embed|episode|server|source)',
    r'/api/(?:v\d+/)?(?:source|server|stream|embed|player)',
    r'/embed[-_]?(?:ajax|api)',
    r'/encrypt[-_]?ajax',
    r'/get[-_]?(?:source|server|stream|video)',
    r'/(?:watch|play|stream)/(?:ajax|api)',
    r'/getSources',
    r'/getEmbed',
    r'/load[-_]?(?:source|server)',
]


# ══════════════════════════════════════════════════════════
#  INITIALIZATION
# ══════════════════════════════════════════════════════════

_REGISTRY_INITIALIZED = False


def init_registry() -> EmbedServiceRegistry:
    """
    Initialize registry dengan semua service data.
    """
    global _REGISTRY_INITIALIZED
    registry = EmbedServiceRegistry.get_instance()

    if _REGISTRY_INITIALIZED and registry.list_all_services():
        return registry

    # Part B: Tier 1 & 2 existing services
    from utils.services_tier1 import register_tier1_services
    register_tier1_services(registry)

    # Part C: New services batch 1
    from utils.services_new_batch1 import register_new_services_batch1
    register_new_services_batch1(registry)

    # Part D: New services batch 2
    from utils.services_new_batch2 import register_new_services_batch2
    register_new_services_batch2(registry)

    # Part E akan tambahkan: register_all_labels(registry)

    _REGISTRY_INITIALIZED = True
    logger.info(f"Registry initialized: {registry}")
    return registry


def get_detector() -> EmbedServiceDetector:
    """Get global detector instance"""
    return EmbedServiceDetector(init_registry())


EMBED_SERVICES = init_registry().list_all_services()
