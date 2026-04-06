"""
services_new_batch1.py — New services batch 1 (high priority)

6 services yang 100% MISSING dari codebase sebelumnya:

  1. voe         — VOE (obfuscated JS, HLS, sangat umum)
  2. vidhide     — VidHide (relatively new, growing fast)
  3. vidguard    — VidGuard (complex wasm-based protection)
  4. vidmoly     — Vidmoly (P.A.C.K.E.R based)
  5. filelions   — FileLions (SEPARATE dari FileMoon)
  6. uqload      — Uqload (SEPARATE dari Upstream)

Structural fixes included:
  • FileLions → entry terpisah + linked ke filemoon_family
  • Uqload → entry terpisah, bukan alias Upstream
"""

from utils.embed_services import (
    EmbedServiceSignature,
    EmbedServiceRegistry,
    DomainEntry,
    DomainStatus,
    ExtractionProfile,
    ExtractionMethod,
    ChallengeType,
    MediaFormat,
    APIEndpoint,
    ServiceTier,
    ServiceReliability,
    FamilyRelation,
    ServiceFamily,
    FamilyMember,
)


# ══════════════════════════════════════════════════════════
#  1. VOE
# ══════════════════════════════════════════════════════════

VOE = EmbedServiceSignature(
    id='voe',
    display_name='VOE',
    family_id='',
    family_relation=FamilyRelation.PARENT,
    aliases=['voesx', 'voe.sx'],

    domains=[
        # Primary
        DomainEntry('voe.sx', DomainStatus.PRIMARY),
        # Common — VOE rotates delivery domains aggressively
        DomainEntry('voe-network.net', DomainStatus.COMMON),
        DomainEntry('launchreliantclever.com', DomainStatus.COMMON,
                     note='Delivery/redirect domain'),
        DomainEntry('reaborativealiede.com', DomainStatus.COMMON,
                     note='Delivery/redirect domain'),
        DomainEntry('precsjusede.com', DomainStatus.COMMON,
                     note='Delivery/redirect domain'),
        DomainEntry('bfrморede.com', DomainStatus.COMMON,
                     note='Delivery/redirect domain'),
        DomainEntry('urochsunloede.com', DomainStatus.COMMON,
                     note='Delivery/redirect domain'),
        DomainEntry('alfrede.com', DomainStatus.COMMON,
                     note='Delivery/redirect domain'),
        DomainEntry('primarycentede.com', DomainStatus.COMMON,
                     note='Delivery/redirect domain'),
        DomainEntry('comaborede.com', DomainStatus.COMMON,
                     note='Delivery/redirect domain'),
        DomainEntry('martinfrede.com', DomainStatus.COMMON,
                     note='Delivery/redirect domain'),
        # Mirror — embed domains
        DomainEntry('robertrelede.com', DomainStatus.MIRROR),
        DomainEntry('citizede.com', DomainStatus.MIRROR),
        DomainEntry('guidecede.com', DomainStatus.MIRROR),
        DomainEntry('perfectcede.com', DomainStatus.MIRROR),
        DomainEntry('markercede.com', DomainStatus.MIRROR),
        DomainEntry('delivede.com', DomainStatus.MIRROR),
        # Legacy
        DomainEntry('voe.bar', DomainStatus.LEGACY),
        DomainEntry('voeunblck.com', DomainStatus.LEGACY),
        DomainEntry('voeunblock.com', DomainStatus.LEGACY),
        DomainEntry('voeunbl0ck.com', DomainStatus.LEGACY),
        DomainEntry('voeun-block.net', DomainStatus.LEGACY),
        # Uncertain — rotate frequently
        DomainEntry('auditorede.com', DomainStatus.UNCERTAIN),
        DomainEntry('assemblede.com', DomainStatus.UNCERTAIN),
        DomainEntry('communede.com', DomainStatus.UNCERTAIN),
    ],

    url_patterns=[
        r'voe\.(?:sx|bar)',
        r'voe(?:un)?bl(?:oc)?k\d*\.(?:com|net)',
        r'voe-network\.net',
        # Delivery domains follow a pattern: *ede.com / *cede.com
        r'[a-z]+(?:rel|cede|ede|rede|sede|mede)\.com',
    ],

    iframe_indicators=[
        'voe.sx', 'voe-network', 'voeunbl', 
        # Delivery domain indicators — partial matches
        'ede.com/e/', 'ede.com/embed/',
    ],
    html_indicators=['voe.sx', 'voe-network', "'hls'"],
    js_indicators=[
        "var sources", "window.location.href", "'hls'",
        "atob(", "prompt", "Hls.loadSource",
    ],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EVAL_JS,
        typical_format=MediaFormat.HLS,

        challenge_types=[
            ChallengeType.OBFUSCATED_JS,
            ChallengeType.BASE64_LAYERS,
            ChallengeType.MULTI_REDIRECT,
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /e/{video_id}",
            "2. Page mungkin redirect ke delivery domain (xxxxede.com)",
            "3. Follow redirect, fetch final embed page",
            "4. Cari JS block yang assign m3u8 URL",
            "5. URL biasanya dalam format:",
            "   a) var sources = {'hls': 'https://....m3u8'} → langsung",
            "   b) var mp4_url = atob('base64...') → perlu decode",
            "   c) eval() chain → perlu execute/unpack",
            "6. Extract m3u8 URL",
            "7. Fetch m3u8 dengan Referer = delivery domain",
        ],

        pseudo_flow=[
            "# Step 1: Handle redirects",
            "resp = fetch(f'https://voe.sx/e/{vid_id}', allow_redirects=True)",
            "final_url = resp.url  # mungkin redirect ke xxxxede.com",
            "html = resp.text",
            "",
            "# Step 2: Method A — direct sources object",
            "m3u8 = regex(r\"'hls'\\s*:\\s*'([^']+\\.m3u8[^']*)'\", html)",
            "",
            "# Step 3: Method B — base64 encoded",
            "if not m3u8:",
            "    b64 = regex(r\"atob\\('([A-Za-z0-9+/=]+)'\\)\", html)",
            "    m3u8 = base64_decode(b64)",
            "",
            "# Step 4: Method C — window.location redirect URL extraction",
            "if not m3u8:",
            "    redirect = regex(r\"window\\.location\\.href\\s*=\\s*'([^']+)'\", html)",
            "    # Follow redirect, re-extract",
            "",
            "# Step 5: Fetch HLS",
            "hls_content = fetch(m3u8, referer=final_url)",
        ],

        key_regex_patterns={
            # Method A: Direct sources
            'sources_hls': r"'hls'\s*:\s*'([^']+\.m3u8[^']*)'",
            'sources_direct': r"'mp4'\s*:\s*'([^']+\.mp4[^']*)'",
            'var_sources': r"var\s+sources\s*=\s*(\{.+?\})",
            
            # Method B: Base64
            'atob_call': r"atob\('([A-Za-z0-9+/=]{20,})'\)",
            'b64_var': r"var\s+\w+\s*=\s*atob\('([^']+)'\)",
            
            # Method C: Redirect
            'window_redirect': r"window\.location\.href\s*=\s*['\"]([^'\"]+)['\"]",
            
            # Delivery URL
            'delivery_m3u8': r"https?://[a-z0-9.-]+/(?:engine|hls)/[^\s'\"]+\.m3u8[^\s'\"]*",
            'delivery_mp4': r"https?://delivery[a-z0-9.-]+/[^\s'\"]+\.mp4[^\s'\"]*",
        },

        decode_methods=['base64', 'follow_redirect'],

        requires_referer=True,
        requires_origin=False,

        header_dependencies={
            'Referer': '{final_embed_url}',
        },

        token_expiry_seconds=10800,  # ~3 jam

        fallback_strategy=(
            "VOE memiliki 3 method extraction (A/B/C). "
            "Coba masing-masing sequential. "
            "Jika semua gagal, gunakan browser capture → "
            "play video dan intercept m3u8 dari network tab."
        ),

        failure_modes=[
            "Delivery domain rotate SANGAT sering (weekly)",
            "Domain pattern: *ede.com, *cede.com, *rede.com",
            "Redirect chain bisa 2-3 hop sebelum embed page final",
            "Base64 encoding bisa multi-layer",
            "m3u8 URL expire setelah ~3 jam",
            "Referer HARUS dari delivery domain, bukan voe.sx",
            "Kadang ada anti-bot check (Cloudflare)",
            "Method extraction berubah antara versi",
        ],

        requires_browser=False,
        browser_note=(
            "Browser capture sebagai last resort. "
            "Klik play, intercept m3u8 dari network."
        ),
    ),

    tier=ServiceTier.TIER_1,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes=(
        'Sangat umum di streaming sites. 3 extraction methods: '
        'direct sources, base64 decode, redirect chain. '
        'Domain rotate aggressively — delivery domains '
        'follow pattern *ede.com/*cede.com.'
    ),
)


# ══════════════════════════════════════════════════════════
#  2. VIDHIDE
# ══════════════════════════════════════════════════════════

VIDHIDE = EmbedServiceSignature(
    id='vidhide',
    display_name='VidHide',
    family_id='vidhide_family',
    family_relation=FamilyRelation.PARENT,
    aliases=['vidhidepro'],

    domains=[
        DomainEntry('vidhide.com', DomainStatus.PRIMARY),
        DomainEntry('vidhidepro.com', DomainStatus.COMMON),
        DomainEntry('vidhideplus.com', DomainStatus.COMMON),
        DomainEntry('vhide.cc', DomainStatus.MIRROR),
        DomainEntry('vidhide.to', DomainStatus.MIRROR),
        DomainEntry('vhstream.com', DomainStatus.MIRROR),
        DomainEntry('vid-hide.com', DomainStatus.UNCERTAIN),
    ],

    url_patterns=[
        r'vid[-]?hide(?:pro|plus)?\.(?:com|to|cc)',
        r'vhide\.cc',
        r'vhstream\.com',
    ],

    iframe_indicators=['vidhide', 'vidhidepro', 'vhide', 'vhstream'],
    html_indicators=['vidhide', 'vhstream'],
    js_indicators=['eval(function(p,a,c,k,e,d)', 'file:"', 'sources:'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EVAL_JS,
        typical_format=MediaFormat.HLS,

        challenge_types=[
            ChallengeType.PACKED_JS,
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /e/{file_code} atau /embed/{file_code}",
            "2. Find eval(function(p,a,c,k,e,d) block di HTML",
            "3. Unpack dengan P.A.C.K.E.R unpacker",
            "4. Extract m3u8 URL dari unpacked JS (key: file:\"...\")",
            "5. Fetch m3u8 dengan Referer = embed URL",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/e/{file_code}')",
            "packed = regex(r'eval\\(function\\(p,a,c,k,e,d\\).+?\\)\\)', html)",
            "unpacked = packer_unpack(packed)",
            "m3u8_url = regex(r'file:\\s*\"([^\"]+\\.m3u8[^\"]*)\"', unpacked)",
            "# Fetch HLS with referer",
            "hls = fetch(m3u8_url, headers={'Referer': embed_url})",
        ],

        key_regex_patterns={
            'packed_js': r'eval\(function\(p,a,c,k,e,d\).+?\)\)',
            'm3u8_url': r'file:\s*"([^"]+\.m3u8[^"]*)"',
            'sources_array': r'sources:\s*\[\{file:"([^"]+)"',
            'poster': r'image:\s*"([^"]+)"',
        },

        decode_methods=['js_unpack_p_a_c_k_e_d'],

        requires_referer=True,
        header_dependencies={
            'Referer': '{embed_url}',
        },

        fallback_strategy=(
            "Pattern identik dengan FileMoon/StreamWish. "
            "Jika P.A.C.K.E.R unpack gagal, coba browser capture."
        ),

        failure_modes=[
            "Pattern sangat mirip FileMoon",
            "Domain relatif baru, mungkin belum stabil",
            "Packed JS format bisa berubah",
            "Referer WAJIB dari domain vidhide",
        ],

        requires_browser=False,
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes=(
        'Relatively new service, growing fast. '
        'Extraction pattern mirip FileMoon family (P.A.C.K.E.R). '
        'Bisa sharing extraction logic.'
    ),
)


# ══════════════════════════════════════════════════════════
#  3. VIDGUARD
# ══════════════════════════════════════════════════════════

VIDGUARD = EmbedServiceSignature(
    id='vidguard',
    display_name='VidGuard',
    family_id='vidguard_family',
    family_relation=FamilyRelation.PARENT,
    aliases=['vgplayer', 'listeamed', 'vgembed', 'v-guard'],

    domains=[
        DomainEntry('vidguard.to', DomainStatus.PRIMARY),
        DomainEntry('vgplayer.com', DomainStatus.COMMON),
        DomainEntry('vgembed.com', DomainStatus.COMMON),
        DomainEntry('listeamed.net', DomainStatus.COMMON,
                     note='Common alias'),
        DomainEntry('vid-guard.com', DomainStatus.MIRROR),
        DomainEntry('vidguard.online', DomainStatus.MIRROR),
        DomainEntry('bembed.net', DomainStatus.MIRROR),
        DomainEntry('vguardcdn.com', DomainStatus.MIRROR,
                     note='CDN/delivery domain'),
        # Uncertain
        DomainEntry('vidguard.cc', DomainStatus.UNCERTAIN),
        DomainEntry('vgfplay.com', DomainStatus.UNCERTAIN),
    ],

    url_patterns=[
        r'vid[-]?guard\.(?:to|com|online|cc)',
        r'vg(?:player|embed|fplay)\.com',
        r'listeamed\.net',
        r'bembed\.net',
        r'vguardcdn\.com',
    ],

    iframe_indicators=[
        'vidguard', 'vgplayer', 'vgembed', 
        'listeamed', 'bembed',
    ],
    html_indicators=['vidguard', 'vgplayer', 'listeamed'],
    js_indicators=[
        'sig:', 'sources:', 'eval(', 
        'WebAssembly', 'wasm', 'decryptSource',
    ],

    extraction=ExtractionProfile(
        method=ExtractionMethod.WASM,
        typical_format=MediaFormat.HLS,

        challenge_types=[
            ChallengeType.WASM_DECRYPT,
            ChallengeType.OBFUSCATED_JS,
            ChallengeType.EVAL_CHAIN,
            ChallengeType.SIGNED_URL,
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /e/{video_id}",
            "2. Page berisi heavily obfuscated JavaScript",
            "3. JS memuat WebAssembly module untuk decryption",
            "4. Encrypted source string ada di HTML/JS",
            "5. WASM module decrypt string → m3u8 URL",
            "6. ALTERNATIF: intercept via browser",
            "   a) Buka embed di browser",
            "   b) Play video",
            "   c) Intercept m3u8 dari network tab",
            "7. m3u8 URL has signed params (expire)",
        ],

        pseudo_flow=[
            "# METHOD 1: JS deobfuscation (SULIT)",
            "html = fetch(f'{host}/e/{vid_id}')",
            "# Find encrypted payload",
            "encrypted = regex(r'sig:\\s*\"([^\"]+)\"', html)",
            "# WASM module path",
            "wasm_url = regex(r'WebAssembly.*?fetch\\(\"([^\"]+\\.wasm)\"', html)",
            "# Download WASM module",
            "wasm_bytes = fetch(f'{host}{wasm_url}').content",
            "# Execute WASM decrypt (COMPLEX — reverse engineering needed)",
            "# ...",
            "",
            "# METHOD 2: Browser intercept (RECOMMENDED)",
            "# Use playwright:",
            "page.goto(embed_url)",
            "# Wait for video to start",
            "# Capture m3u8 from network tab",
            "m3u8_urls = [req.url for req in captured if '.m3u8' in req.url]",
        ],

        key_regex_patterns={
            'sig_payload': r'sig:\s*"([^"]+)"',
            'wasm_url': r'(?:WebAssembly|wasm).*?["\']([^"\']+\.wasm)["\']',
            'eval_block': r'eval\((.+?)\)\s*;',
            'sources_encrypted': r'sources:\s*\[\{["\']?file["\']?\s*:\s*["\']([^"\']+)',
            'decrypt_function': r'function\s+decrypt\w*\s*\(',
        },

        decode_methods=[
            'wasm_execute',
            'js_deobfuscate',
            'browser_intercept',
        ],

        requires_referer=True,
        requires_origin=True,

        header_dependencies={
            'Referer': '{embed_url}',
            'Origin': '{embed_origin}',
        },

        token_expiry_seconds=7200,  # ~2 jam

        fallback_strategy=(
            "WASM decryption sangat sulit di-reverse. "
            "RECOMMENDED: gunakan browser capture method. "
            "Buka embed URL di Playwright, click play, "
            "intercept m3u8 URL dari network requests. "
            "Ini paling reliable untuk VidGuard."
        ),

        failure_modes=[
            "WASM module = reverse engineering yang SANGAT sulit",
            "JS heavily obfuscated, berubah tiap versi",
            "Eval chain bisa 3-5 layers deep",
            "Signed URL expire setelah ~2 jam",
            "Domain rotate moderate",
            "Anti-bot (Cloudflare) pada beberapa domain",
            "Referer + Origin WAJIB",
            "IP-lock pada beberapa m3u8 URLs",
        ],

        requires_browser=True,
        browser_note=(
            "Browser capture adalah method PALING RELIABLE "
            "untuk VidGuard. WASM reverse engineering tidak "
            "practical untuk automated extraction. "
            "Playwright: goto → play → capture m3u8."
        ),
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.LOW,
    last_verified='2024-12-01',
    notes=(
        'PALING SULIT di-extract secara automated. '
        'Menggunakan WebAssembly untuk decrypt source URLs. '
        'Browser capture adalah satu-satunya method reliable. '
        'Treat sebagai "browser-required" service.'
    ),
)


# ══════════════════════════════════════════════════════════
#  4. VIDMOLY
# ══════════════════════════════════════════════════════════

VIDMOLY = EmbedServiceSignature(
    id='vidmoly',
    display_name='Vidmoly',
    family_id='',
    family_relation=FamilyRelation.PARENT,
    aliases=['vmoly'],

    domains=[
        DomainEntry('vidmoly.to', DomainStatus.PRIMARY),
        DomainEntry('vidmoly.me', DomainStatus.COMMON),
        DomainEntry('vidmoly.com', DomainStatus.MIRROR),
        DomainEntry('vidmoly.net', DomainStatus.UNCERTAIN),
    ],

    url_patterns=[
        r'vidmoly\.(?:to|me|com|net)',
    ],

    iframe_indicators=['vidmoly'],
    html_indicators=['vidmoly'],
    js_indicators=['eval(function(p,a,c,k,e,d)', 'file:"', 'sources:'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EVAL_JS,
        typical_format=MediaFormat.HLS,

        challenge_types=[
            ChallengeType.PACKED_JS,
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /embed-{file_code} atau /w/{file_code}",
            "2. Find eval(function(p,a,c,k,e,d) block",
            "3. Unpack dengan P.A.C.K.E.R unpacker",
            "4. Extract m3u8 URL dari unpacked JS",
            "5. Fetch m3u8 dengan Referer header",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/embed-{file_code}')",
            "# ATAU: html = fetch(f'{host}/w/{file_code}')",
            "packed = regex(r'eval\\(function\\(p,a,c,k,e,d\\).+?\\)\\)', html)",
            "unpacked = packer_unpack(packed)",
            "m3u8_url = regex(r'file:\\s*\"([^\"]+\\.m3u8[^\"]*)\"', unpacked)",
            "# ATAU: sources array format",
            "m3u8_url = regex(r'sources:\\[\\{src:\"([^\"]+)\"', unpacked)",
        ],

        key_regex_patterns={
            'packed_js': r'eval\(function\(p,a,c,k,e,d\).+?\)\)',
            'm3u8_url': r'file:\s*"([^"]+\.m3u8[^"]*)"',
            'sources_src': r'sources:\[\{src:"([^"]+)"',
            'poster': r'image:\s*"([^"]+)"',
        },

        decode_methods=['js_unpack_p_a_c_k_e_d'],

        requires_referer=True,
        header_dependencies={
            'Referer': '{embed_url}',
        },

        fallback_strategy=(
            "Pattern identik dengan FileMoon family. "
            "Shared P.A.C.K.E.R unpacker logic."
        ),

        failure_modes=[
            "Standard P.A.C.K.E.R pattern",
            "Embed path bisa /embed- atau /w/",
            "Referer WAJIB dari domain vidmoly",
        ],

        requires_browser=False,
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes=(
        'P.A.C.K.E.R based — same pattern as FileMoon, '
        'StreamWish, VidHide. Shared extraction logic.'
    ),
)


# ══════════════════════════════════════════════════════════
#  5. FILELIONS (SEPARATE from FileMoon)
# ══════════════════════════════════════════════════════════

FILELIONS = EmbedServiceSignature(
    id='filelions',
    display_name='FileLions',
    family_id='filemoon_family',
    family_relation=FamilyRelation.SIBLING,
    aliases=['flions', 'alions'],

    domains=[
        DomainEntry('filelions.to', DomainStatus.PRIMARY),
        DomainEntry('filelions.live', DomainStatus.COMMON),
        DomainEntry('filelions.com', DomainStatus.COMMON),
        DomainEntry('filelions.online', DomainStatus.MIRROR),
        DomainEntry('alions.pro', DomainStatus.COMMON,
                     note='Common alias, same backend'),
        DomainEntry('filelions.site', DomainStatus.MIRROR),
        DomainEntry('filelions.xyz', DomainStatus.UNCERTAIN),
        DomainEntry('dlions.pro', DomainStatus.UNCERTAIN),
    ],

    url_patterns=[
        r'filelions\.(?:to|live|com|online|site|xyz)',
        r'[a-d]lions\.pro',
    ],

    iframe_indicators=['filelions', 'alions'],
    html_indicators=['filelions', 'alions.pro'],
    js_indicators=['eval(function(p,a,c,k,e,d)', 'file:"'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EVAL_JS,
        typical_format=MediaFormat.HLS,

        challenge_types=[
            ChallengeType.PACKED_JS,
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /e/{file_code}",
            "2. Find eval(function(p,a,c,k,e,d) block",
            "3. Unpack dengan P.A.C.K.E.R unpacker",
            "4. Extract m3u8 URL dari unpacked JS",
            "5. Fetch m3u8 dengan Referer = embed URL",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/e/{file_code}')",
            "packed = regex(r'eval\\(function\\(p,a,c,k,e,d\\).+?\\)\\)', html)",
            "unpacked = packer_unpack(packed)",
            "m3u8_url = regex(r'file:\\s*\"([^\"]+\\.m3u8[^\"]*)\"', unpacked)",
        ],

        key_regex_patterns={
            'packed_js': r'eval\(function\(p,a,c,k,e,d\).+?\)\)',
            'm3u8_url': r'file:\s*"([^"]+\.m3u8[^"]*)"',
            'sources_array': r'sources:\s*\[\{file:"([^"]+)"',
        },

        decode_methods=['js_unpack_p_a_c_k_e_d'],

        requires_referer=True,
        header_dependencies={
            'Referer': '{embed_url}',
        },

        fallback_strategy=(
            "Identik dengan FileMoon extraction. "
            "Bisa pakai shared P.A.C.K.E.R unpacker."
        ),

        failure_modes=[
            "Pattern identik FileMoon tapi DOMAIN BERBEDA",
            "Jangan confuse dengan FileMoon — ini service terpisah",
            "Domain alions.pro sering dipakai sebagai primary",
            "Packed JS format bisa sedikit berbeda dari FileMoon",
        ],

        requires_browser=False,
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes=(
        'SIBLING dari FileMoon — BUKAN alias. '
        'Service terpisah dengan domain pool berbeda. '
        'Extraction method identik (P.A.C.K.E.R) tapi '
        'backend & CDN berbeda. '
        'Di banyak site, FileLions dan FileMoon muncul '
        'sebagai 2 server TERPISAH.'
    ),
)


# ══════════════════════════════════════════════════════════
#  6. UQLOAD (SEPARATE from Upstream)
# ══════════════════════════════════════════════════════════

UQLOAD = EmbedServiceSignature(
    id='uqload',
    display_name='Uqload',
    family_id='',
    family_relation=FamilyRelation.PARENT,
    aliases=['uqload'],

    domains=[
        DomainEntry('uqload.com', DomainStatus.PRIMARY),
        DomainEntry('uqload.to', DomainStatus.COMMON),
        DomainEntry('uqload.co', DomainStatus.MIRROR),
        DomainEntry('uqload.net', DomainStatus.UNCERTAIN),
        DomainEntry('uqload.org', DomainStatus.UNCERTAIN),
        DomainEntry('uqload.io', DomainStatus.UNCERTAIN),
    ],

    url_patterns=[
        r'uqload\.(?:com|to|co|net|org|io)',
    ],

    iframe_indicators=['uqload'],
    html_indicators=['uqload'],
    js_indicators=['eval(function(p,a,c,k,e,d)', 'sources:', 'Clappr.Player'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EVAL_JS,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.PACKED_JS,
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /embed-{file_code}.html",
            "2. Find eval(function(p,a,c,k,e,d) block",
            "3. Unpack dengan P.A.C.K.E.R unpacker",
            "4. Extract direct MP4 URL dari unpacked JS",
            "5. ATAU: find <source> tag / sources array",
            "6. Download MP4 dengan Referer header",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/embed-{file_code}.html')",
            "",
            "# Method 1: P.A.C.K.E.R",
            "packed = regex(r'eval\\(function\\(p,a,c,k,e,d\\).+?\\)\\)', html)",
            "if packed:",
            "    unpacked = packer_unpack(packed)",
            "    mp4_url = regex(r'sources:\\s*\\[\\{file:\\s*\"([^\"]+)\"', unpacked)",
            "    # ATAU:",
            "    mp4_url = regex(r'src:\\s*\"([^\"]+\\.mp4[^\"]*)\"', unpacked)",
            "",
            "# Method 2: Direct source tag (beberapa versi)",
            "if not mp4_url:",
            "    mp4_url = regex(r'<source src=\"([^\"]+)\" type=\"video/mp4\"', html)",
            "",
            "# Method 3: Clappr player config",
            "if not mp4_url:",
            "    mp4_url = regex(r'source:\\s*\"([^\"]+)\"', html)",
        ],

        key_regex_patterns={
            'packed_js': r'eval\(function\(p,a,c,k,e,d\).+?\)\)',
            'mp4_url': r'src:\s*"([^"]+\.mp4[^"]*)"',
            'sources_file': r'sources:\s*\[\{file:\s*"([^"]+)"',
            'source_tag': r'<source src="([^"]+)" type="video/mp4"',
            'clappr_source': r'source:\s*"([^"]+)"',
        },

        decode_methods=['js_unpack_p_a_c_k_e_d'],

        requires_referer=True,
        header_dependencies={
            'Referer': '{embed_url}',
        },

        fallback_strategy=(
            "3 extraction methods: P.A.C.K.E.R unpack, "
            "direct source tag, Clappr config. "
            "Coba sequential."
        ),

        failure_modes=[
            "Bukan Upstream — ini service TERPISAH",
            "Kadang pakai Clappr player (bukan JWPlayer)",
            "Direct URL kadang IP-locked",
            "Format embed path: /embed-{code}.html",
        ],

        requires_browser=False,
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes=(
        'BUKAN alias Upstream — service terpisah. '
        'Punya domain pool sendiri dan behavior berbeda. '
        'Kadang pakai Clappr player, bukan JWPlayer. '
        'Multiple extraction fallbacks available.'
    ),
)


# ══════════════════════════════════════════════════════════
#  FAMILY UPDATES
# ══════════════════════════════════════════════════════════

# Update filemoon_family untuk include FileLions
FILEMOON_FAMILY_UPDATE = ServiceFamily(
    family_id='filemoon_family',
    display_name='FileMoon Family',
    members=[
        FamilyMember('filemoon', FamilyRelation.PARENT),
        FamilyMember('filelions', FamilyRelation.SIBLING,
                     note='Separate service, same P.A.C.K.E.R extraction pattern'),
    ],
    shared_extraction_method=ExtractionMethod.EVAL_JS,
    shared_api_pattern='P.A.C.K.E.R unpack → file:"m3u8_url"',
    note=(
        'FileMoon dan FileLions share extraction method (P.A.C.K.E.R) '
        'tapi domain pool, CDN, dan backend BERBEDA. '
        'Treat sebagai 2 service terpisah yang bisa share unpacker logic.'
    ),
)

VIDGUARD_FAMILY = ServiceFamily(
    family_id='vidguard_family',
    display_name='VidGuard Family',
    members=[
        FamilyMember('vidguard', FamilyRelation.PARENT),
    ],
    shared_extraction_method=ExtractionMethod.WASM,
    note='WASM-based protection. Browser capture recommended.',
)

VIDHIDE_FAMILY = ServiceFamily(
    family_id='vidhide_family',
    display_name='VidHide Family',
    members=[
        FamilyMember('vidhide', FamilyRelation.PARENT),
    ],
    shared_extraction_method=ExtractionMethod.EVAL_JS,
)


# ══════════════════════════════════════════════════════════
#  COLLECTIONS
# ══════════════════════════════════════════════════════════

ALL_SERVICES_BATCH1 = [
    VOE,
    VIDHIDE,
    VIDGUARD,
    VIDMOLY,
    FILELIONS,
    UQLOAD,
]

ALL_FAMILIES_BATCH1 = [
    FILEMOON_FAMILY_UPDATE,   # Override existing filemoon_family
    VIDGUARD_FAMILY,
    VIDHIDE_FAMILY,
]


# ══════════════════════════════════════════════════════════
#  REGISTRATION
# ══════════════════════════════════════════════════════════

def register_new_services_batch1(
    registry: EmbedServiceRegistry = None
):
    """
    Register 6 new services + family updates.
    Dipanggil dari init_registry() setelah tier1 services.
    """
    if registry is None:
        registry = EmbedServiceRegistry.get_instance()

    # Register updated families (override existing)
    for family in ALL_FAMILIES_BATCH1:
        registry.register_family(family)

    # Register new services
    registry.register_services(ALL_SERVICES_BATCH1)

    return registry