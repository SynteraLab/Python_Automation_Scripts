"""
services_tier1.py — Service definitions Tier 1 & 2 (existing, upgraded to v2)

Structural fixes:
  • FileMoon CLEAN — tanpa FileLions (Part C)
  • Upstream CLEAN — tanpa Uqload (Part C)
  • Domain status classification per entry
  • ExtractionProfile lengkap per service
  • Family definitions

Services in this file (14):
  1.  vidcloud      — VidCloud / RapidCloud / MCloud
  2.  vidplay       — VidPlay / MyCloud
  3.  filemoon      — FileMoon (CLEAN)
  4.  streamtape    — StreamTape
  5.  doodstream    — DoodStream
  6.  mp4upload     — Mp4Upload
  7.  streamsb      — StreamSB family
  8.  mixdrop       — MixDrop
  9.  streamwish    — StreamWish
  10. vidoza        — Vidoza
  11. upstream      — Upstream (CLEAN)
  12. okru          — OK.ru
  13. sendvid       — SendVid
  14. fembed        — Fembed / Fcdn
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
#  1. VIDCLOUD / RAPIDCLOUD / MCLOUD
# ══════════════════════════════════════════════════════════

VIDCLOUD = EmbedServiceSignature(
    id='vidcloud',
    display_name='VidCloud',
    family_id='vidcloud_family',
    family_relation=FamilyRelation.PARENT,
    aliases=['vizcloud', 'rapid-cloud', 'mcloud', 'rabbitstream'],

    domains=[
        # Primary
        DomainEntry('rapid-cloud.co', DomainStatus.PRIMARY),
        # Common
        DomainEntry('rapid-cloud.ru', DomainStatus.COMMON),
        DomainEntry('rabbitstream.net', DomainStatus.COMMON),
        # VizCloud variants
        DomainEntry('vidcloud.co', DomainStatus.COMMON),
        DomainEntry('vizcloud.co', DomainStatus.COMMON),
        DomainEntry('vizcloud2.com', DomainStatus.MIRROR),
        DomainEntry('vizcloud.info', DomainStatus.MIRROR),
        DomainEntry('vizcloud.online', DomainStatus.MIRROR),
        DomainEntry('vizcloud.xyz', DomainStatus.MIRROR),
        DomainEntry('vizcloud.digital', DomainStatus.MIRROR),
        # MCloud variants
        DomainEntry('mcloud.to', DomainStatus.COMMON),
        DomainEntry('mcloud2.to', DomainStatus.MIRROR),
        DomainEntry('mcloud.bz', DomainStatus.MIRROR),
        # Legacy
        DomainEntry('vidcloud9.com', DomainStatus.LEGACY),
        DomainEntry('vidcloud.pro', DomainStatus.LEGACY),
    ],

    url_patterns=[
        r'(?:vid|viz)cloud\d*\.(?:co|com|pro|info|online|xyz|digital)',
        r'mcloud\d*\.(?:to|bz)',
        r'rapid-cloud\.(?:co|ru)',
        r'rabbitstream\.(?:net|com)',
    ],

    iframe_indicators=['rapid-cloud', 'rabbitstream', 'vidcloud', 'vizcloud', 'mcloud'],
    html_indicators=['rapidcloud', 'getSources'],
    js_indicators=['e]getSources', 'recaptchaKey', 'loadSource'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EMBED_API,
        typical_format=MediaFormat.HLS,

        challenge_types=[
            ChallengeType.ENCRYPTED_AJAX,
            ChallengeType.SIGNED_URL,
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page: /embed-6/{video_id}",
            "2. Extract recaptcha key + data-id dari HTML",
            "3. GET /ajax/embed-6/getSources?id={data_id}",
            "4. Response JSON berisi encrypted 'sources' string",
            "5. Decrypt sources (AES key bisa berubah)",
            "6. Parse JSON → extract m3u8 URL",
            "7. Fetch m3u8 dengan Referer header",
        ],

        pseudo_flow=[
            "html = fetch(f'{embed_host}/embed-6/{vid_id}')",
            "data_id = regex(r'data-id=\"([^\"]+)\"', html)",
            "api_url = f'{embed_host}/ajax/embed-6/getSources?id={data_id}'",
            "resp = fetch(api_url, headers={'X-Requested-With': 'XMLHttpRequest'})",
            "json_data = resp.json()",
            "if json_data.get('encrypted'):",
            "    sources = aes_decrypt(json_data['sources'], key)",
            "else:",
            "    sources = json_data['sources']",
            "m3u8_url = sources[0]['file']",
        ],

        key_regex_patterns={
            'data_id': r'data-id="([^"]+)"',
            'recaptcha_key': r'recaptchaKey\s*=\s*["\']([^"\']+)',
        },

        decode_methods=['aes_cbc_decrypt'],

        requires_referer=True,
        requires_origin=True,

        header_dependencies={
            'Referer': '{embed_url}',
            'X-Requested-With': 'XMLHttpRequest',
        },

        api_endpoints=[
            APIEndpoint(
                path='/ajax/embed-6/getSources',
                method='GET',
                purpose='get_sources',
                id_param='id',
                required_headers={
                    'X-Requested-With': 'XMLHttpRequest',
                },
                response_format='json',
                media_key_paths=['sources[0].file', 'sources'],
                note='Response mungkin encrypted (AES), cek field "encrypted"',
            ),
        ],

        token_expiry_seconds=7200,  # ~2 jam

        fallback_strategy=(
            "Jika getSources API gagal, coba browser capture "
            "— play video dan intercept m3u8 dari network tab"
        ),

        failure_modes=[
            "AES decryption key berubah secara periodik",
            "ReCAPTCHA bisa muncul setelah beberapa request",
            "m3u8 URL expire setelah ~2 jam",
            "Referer WAJIB dari domain embed",
            "Beberapa server butuh specific User-Agent",
        ],

        requires_browser=False,
        browser_note="Browser fallback jika API decryption gagal",
    ),

    tier=ServiceTier.TIER_1,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes='Paling umum di anime/movie sites. AES key sering berubah.',
)


# ══════════════════════════════════════════════════════════
#  2. VIDPLAY / MYCLOUD
# ══════════════════════════════════════════════════════════

VIDPLAY = EmbedServiceSignature(
    id='vidplay',
    display_name='VidPlay',
    family_id='vidcloud_family',
    family_relation=FamilyRelation.SIBLING,
    aliases=['mycloud', 'vidstream'],

    domains=[
        DomainEntry('vidplay.site', DomainStatus.PRIMARY),
        DomainEntry('vidplay.online', DomainStatus.COMMON),
        DomainEntry('vidplay.lol', DomainStatus.COMMON),
        DomainEntry('mycloud.to', DomainStatus.COMMON),
        DomainEntry('vidstream.pro', DomainStatus.MIRROR),
        DomainEntry('vidstreamz.online', DomainStatus.MIRROR),
        DomainEntry('vid2play.com', DomainStatus.LEGACY),
    ],

    url_patterns=[
        r'vidplay\.(?:site|online|lol)',
        r'mycloud\.to',
        r'vidstream(?:z)?\.(?:pro|online)',
    ],

    iframe_indicators=['vidplay', 'mycloud'],
    html_indicators=['futoken', 'vidplay'],
    js_indicators=['futoken', 'rawUrl'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EMBED_API,
        typical_format=MediaFormat.HLS,

        challenge_types=[
            ChallengeType.OBFUSCATED_JS,
            ChallengeType.TOKEN_ROTATION,
            ChallengeType.SIGNED_URL,
        ],

        flow_steps=[
            "1. Fetch embed page /e/{video_id}",
            "2. GET /futoken endpoint untuk generate token",
            "3. Build source URL dengan token",
            "4. GET source URL → JSON dengan m3u8",
            "5. Fetch m3u8 dengan Referer header",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/e/{vid_id}')",
            "futoken_url = f'{host}/futoken'",
            "token_resp = fetch(futoken_url, referer=embed_url)",
            "# Build encoded URL dari futoken response",
            "source_url = build_source_url(token_resp, vid_id)",
            "sources = fetch(source_url).json()",
            "m3u8 = sources['result']['sources'][0]['file']",
        ],

        requires_referer=True,
        header_dependencies={
            'Referer': '{embed_url}',
        },

        api_endpoints=[
            APIEndpoint(
                path='/futoken',
                method='GET',
                purpose='get_token',
                requires_id=False,
                response_format='text',
                note='Returns JS/token yang perlu diproses',
            ),
        ],

        token_expiry_seconds=3600,

        failure_modes=[
            "futoken endpoint bisa return HTML bukan token",
            "Token generation logic berubah periodik",
            "Referer harus exact match",
        ],

        requires_browser=False,
        browser_note="Browser capture sebagai fallback",
    ),

    tier=ServiceTier.TIER_1,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
)


# ══════════════════════════════════════════════════════════
#  3. FILEMOON (CLEAN — tanpa FileLions)
# ══════════════════════════════════════════════════════════

FILEMOON = EmbedServiceSignature(
    id='filemoon',
    display_name='FileMoon',
    family_id='filemoon_family',
    family_relation=FamilyRelation.PARENT,
    aliases=['fmoon'],

    domains=[
        DomainEntry('filemoon.sx', DomainStatus.PRIMARY),
        DomainEntry('filemoon.to', DomainStatus.COMMON),
        DomainEntry('filemoon.in', DomainStatus.COMMON),
        DomainEntry('filemoon.link', DomainStatus.MIRROR),
        DomainEntry('kerapoxy.cc', DomainStatus.MIRROR,
                     note='Rebrand/alias of filemoon'),
        # REMOVED: filelions.to, filelions.live, alions.pro
        # → Pindah ke Part C sebagai service terpisah
    ],

    url_patterns=[
        r'filemoon\.(?:sx|to|in|link)',
        r'kerapoxy\.cc',
    ],

    iframe_indicators=['filemoon', 'kerapoxy'],
    html_indicators=['filemoon', 'kerapoxy'],
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
            "2. Find eval(function(p,a,c,k,e,d) block di HTML",
            "3. Unpack dengan P.A.C.K.E.R unpacker",
            "4. Extract m3u8 URL dari unpacked JS",
            "5. Fetch m3u8 dengan Referer = embed URL",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/e/{file_code}')",
            "packed = regex(r'eval\\(function\\(p,a,c,k,e,d\\).+?\\)\\)', html)",
            "unpacked = packer_unpack(packed)",
            "m3u8_url = regex(r'file:\\s*\"([^\"]+\\.m3u8[^\"]*)\"', unpacked)",
            "# ATAU:",
            "m3u8_url = regex(r'sources:\\s*\\[\\{file:\"([^\"]+)\"', unpacked)",
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
            "Jika PACKER unpack gagal, coba browser capture "
            "— play video dan intercept m3u8 dari network"
        ),

        failure_modes=[
            "Packed JS format bisa berubah",
            "Referer WAJIB dari domain filemoon",
            "Beberapa mirror pakai proteksi tambahan",
            "m3u8 URL mungkin signed/expiring",
        ],
    ),

    tier=ServiceTier.TIER_1,
    reliability=ServiceReliability.HIGH,
    last_verified='2024-12-01',
    notes='Sangat umum. P.A.C.K.E.R unpacker cukup stabil.',
)


# ══════════════════════════════════════════════════════════
#  4. STREAMTAPE
# ══════════════════════════════════════════════════════════

STREAMTAPE = EmbedServiceSignature(
    id='streamtape',
    display_name='StreamTape',
    family_id='streamtape_family',
    family_relation=FamilyRelation.PARENT,
    aliases=['strtape', 'stape', 'tape'],

    domains=[
        DomainEntry('streamtape.com', DomainStatus.PRIMARY),
        DomainEntry('streamtape.to', DomainStatus.COMMON),
        DomainEntry('streamtape.cc', DomainStatus.COMMON),
        DomainEntry('streamtape.xyz', DomainStatus.MIRROR),
        DomainEntry('streamtape.net', DomainStatus.MIRROR),
        DomainEntry('strtape.cloud', DomainStatus.COMMON),
        DomainEntry('stape.fun', DomainStatus.COMMON),
        DomainEntry('tapeadsenjoyer.com', DomainStatus.MIRROR,
                     note='Anti-adblock variant'),
        DomainEntry('tapeblocker.com', DomainStatus.MIRROR,
                     note='Anti-adblock variant'),
        DomainEntry('tapewithadblock.org', DomainStatus.MIRROR),
        DomainEntry('tape.farm', DomainStatus.UNCERTAIN),
    ],

    url_patterns=[
        r'str(?:eam)?tape\.(?:com|to|cc|xyz|net)',
        r'strtape\.cloud',
        r'stape\.fun',
        r'tape(?:adsenjoyer|blocker|withadblock)\.(?:com|org)',
        r'tape\.farm',
    ],

    iframe_indicators=['streamtape', 'strtape', 'stape', 'tape'],
    html_indicators=['robotstoken', 'streamtape'],
    js_indicators=["document.getElementById('robotstoken')", 'videolink'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.API,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.OBFUSCATED_JS,
            ChallengeType.TOKEN_ROTATION,
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /e/{video_id}",
            "2. Find div#robotstoken atau div#ideoooolink",
            "3. Extract partial URL dari div inner text",
            "4. Find JS yang build full URL (concatenation)",
            "5. GET /get_video?id=...&expires=...&ip=...&token=...",
            "6. Response redirect ke direct MP4 URL",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/e/{vid_id}')",
            "# Method 1: robotstoken",
            "token_div = regex(r'id=\"robotstoken\"[^>]*>([^<]+)', html)",
            "# Method 2: JS URL building",
            "js_lines = regex(r\"document\\.getElementById\\('[^']+url'\\)\\.innerHTML\\s*=.+\", html)",
            "# Build final URL from token + JS concatenation",
            "video_url = build_streamtape_url(token_div, js_lines)",
            "# URL is direct MP4 download",
        ],

        key_regex_patterns={
            'robotstoken': r'id="robotstoken"[^>]*>([^<]+)',
            'ideoolink': r'id="ideoooolink"[^>]*>([^<]+)',
            'js_url_build': r"document\.getElementById\('[^']+url'\)\.innerHTML\s*=\s*(.+?)(?:;|\n)",
            'get_video': r'/get_video\?[^"\']+',
        },

        requires_referer=True,
        header_dependencies={
            'Referer': '{embed_url}',
        },

        token_expiry_seconds=14400,  # ~4 jam

        fallback_strategy=(
            "Jika JS parsing gagal, coba browser execute "
            "dan intercept /get_video request"
        ),

        failure_modes=[
            "URL building JS sering berubah format",
            "Domain sering rotate (tapeadsenjoyer, tapeblocker, dll)",
            "Token expire setelah ~4 jam",
            "Anti-adblock variants bisa block automated requests",
            "Referer harus dari domain streamtape",
        ],
    ),

    tier=ServiceTier.TIER_1,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes='Sangat umum. JS URL building logic sering berubah.',
)


# ══════════════════════════════════════════════════════════
#  5. DOODSTREAM
# ══════════════════════════════════════════════════════════

DOODSTREAM = EmbedServiceSignature(
    id='doodstream',
    display_name='DoodStream',
    family_id='dood_family',
    family_relation=FamilyRelation.PARENT,
    aliases=['dood', 'ds2play', 'doods'],

    domains=[
        DomainEntry('doodstream.com', DomainStatus.PRIMARY),
        DomainEntry('dood.to', DomainStatus.COMMON),
        DomainEntry('dood.la', DomainStatus.COMMON),
        DomainEntry('dood.so', DomainStatus.COMMON),
        DomainEntry('dood.pm', DomainStatus.COMMON),
        DomainEntry('dood.wf', DomainStatus.COMMON),
        DomainEntry('dood.cx', DomainStatus.MIRROR),
        DomainEntry('dood.sh', DomainStatus.MIRROR),
        DomainEntry('dood.watch', DomainStatus.MIRROR),
        DomainEntry('dood.re', DomainStatus.MIRROR),
        DomainEntry('dood.yt', DomainStatus.MIRROR),
        DomainEntry('dood.ws', DomainStatus.MIRROR),
        DomainEntry('ds2play.com', DomainStatus.COMMON),
        DomainEntry('doods.pro', DomainStatus.COMMON),
        DomainEntry('doodapi.com', DomainStatus.MIRROR,
                     note='API domain'),
        DomainEntry('d0000d.com', DomainStatus.MIRROR),
        DomainEntry('d0o0d.com', DomainStatus.MIRROR),
        DomainEntry('do0od.com', DomainStatus.UNCERTAIN),
    ],

    url_patterns=[
        r'dood(?:stream)?\.(?:com|to|la|pm|so|wf|cx|sh|watch|re|yt|ws)',
        r'doodapi\.com',
        r'ds2play\.com',
        r'doods\.pro',
        r'd[0o]+d\.com',
    ],

    iframe_indicators=['dood', 'ds2play', 'doods'],
    html_indicators=['doodstream', '/pass_md5/'],
    js_indicators=['pass_md5', 'dsplayer', 'makePlay'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.API,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.TOKEN_ROTATION,
            ChallengeType.SIGNED_URL,
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /e/{video_id}",
            "2. Extract /pass_md5/{token} URL dari JS",
            "3. GET /pass_md5/{token} → partial direct URL",
            "4. Append random string + expiry params",
            "5. Final URL = partial_url + random + ?token=...&expiry=...",
            "6. GET final URL dengan Referer = embed page",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/e/{vid_id}')",
            "pass_md5_url = regex(r\"/pass_md5/[^'\\\"]+\", html)",
            "partial_url = fetch(f'{host}{pass_md5_url}', referer=embed_url).text",
            "import random, string, time",
            "rand = ''.join(random.choices(string.ascii_letters + string.digits, k=10))",
            "token = pass_md5_url.split('/')[-1]",
            "expiry = int(time.time() * 1000)",
            "video_url = f'{partial_url}{rand}?token={token}&expiry={expiry}'",
        ],

        key_regex_patterns={
            'pass_md5': r"['\"](/pass_md5/[^'\"]+)['\"]",
            'make_play': r'makePlay\s*\(.+?\)',
        },

        requires_referer=True,
        header_dependencies={
            'Referer': '{embed_url}',
        },

        token_expiry_seconds=300,  # ~5 menit, sangat pendek!

        failure_modes=[
            "Token expire SANGAT CEPAT (~5 menit)",
            "Domain rotate sangat sering",
            "Referer harus exact match",
            "Random string harus 10 chars alphanumeric",
            "IP-locked: token hanya valid untuk IP yang request",
        ],

        requires_browser=False,
    ),

    tier=ServiceTier.TIER_1,
    reliability=ServiceReliability.LOW,
    last_verified='2024-12-01',
    notes='Domain paling sering rotate. Token sangat short-lived.',
)


# ══════════════════════════════════════════════════════════
#  6. MP4UPLOAD
# ══════════════════════════════════════════════════════════

MP4UPLOAD = EmbedServiceSignature(
    id='mp4upload',
    display_name='Mp4Upload',
    family_id='',
    aliases=['mp4up'],

    domains=[
        DomainEntry('mp4upload.com', DomainStatus.PRIMARY),
        DomainEntry('www.mp4upload.com', DomainStatus.PRIMARY),
    ],

    url_patterns=[r'mp4upload\.com'],

    iframe_indicators=['mp4upload'],
    html_indicators=['mp4upload', 'player_498586'],
    js_indicators=['player_498586', 'eval(function(p,a,c,k,e,d)'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EVAL_JS,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.PACKED_JS,
        ],

        flow_steps=[
            "1. Fetch embed page /embed-{video_id}.html",
            "2. Find eval(function(p,a,c,k,e,d) block",
            "3. Unpack P.A.C.K.E.R",
            "4. Extract direct MP4 URL dari unpacked JS",
            "5. Download MP4 langsung",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/embed-{vid_id}.html')",
            "packed = regex(r'eval\\(function\\(p,a,c,k,e,d\\).+?\\)\\)', html)",
            "unpacked = packer_unpack(packed)",
            "mp4_url = regex(r'src:\"([^\"]+\\.mp4[^\"]*)\"', unpacked)",
            "# ATAU:",
            "mp4_url = regex(r'player\\.src\\(\"([^\"]+)\"\\)', unpacked)",
        ],

        key_regex_patterns={
            'packed_js': r'eval\(function\(p,a,c,k,e,d\).+?\)\)',
            'mp4_url': r'src:"([^"]+\.mp4[^"]*)"',
            'player_src': r'player\.src\("([^"]+)"\)',
        },

        decode_methods=['js_unpack_p_a_c_k_e_d'],

        requires_referer=True,
        header_dependencies={
            'Referer': '{embed_url}',
        },

        failure_modes=[
            "Packed JS bisa berubah format",
            "Direct URL kadang IP-locked",
        ],
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.HIGH,
    last_verified='2024-12-01',
    notes='Simpel dan stabil. P.A.C.K.E.R unpack standard.',
)


# ══════════════════════════════════════════════════════════
#  7. STREAMSB FAMILY
# ══════════════════════════════════════════════════════════

STREAMSB = EmbedServiceSignature(
    id='streamsb',
    display_name='StreamSB',
    family_id='streamsb_family',
    family_relation=FamilyRelation.PARENT,
    aliases=[
        'sbembed', 'watchsb', 'embedsb', 'sbplay',
        'sbvideo', 'cloudemb', 'tubesb', 'playersb',
        'lvturbo', 'sbfull', 'sbspeed', 'sbhight',
        'sbthe', 'ssbstream', 'sbfast', 'sbani',
        'sbrity',
    ],

    domains=[
        # Primary
        DomainEntry('streamsb.net', DomainStatus.PRIMARY),
        DomainEntry('embedsb.com', DomainStatus.PRIMARY),
        # Common
        DomainEntry('streamsb.com', DomainStatus.COMMON),
        DomainEntry('watchsb.com', DomainStatus.COMMON),
        DomainEntry('sbplay.org', DomainStatus.COMMON),
        DomainEntry('sbplay1.com', DomainStatus.COMMON),
        DomainEntry('cloudemb.com', DomainStatus.COMMON),
        DomainEntry('playersb.com', DomainStatus.COMMON),
        DomainEntry('lvturbo.com', DomainStatus.COMMON),
        # Mirror
        DomainEntry('sbembed.com', DomainStatus.MIRROR),
        DomainEntry('sbembed1.com', DomainStatus.MIRROR),
        DomainEntry('sbembed2.com', DomainStatus.MIRROR),
        DomainEntry('sbvideo.net', DomainStatus.MIRROR),
        DomainEntry('sbplay2.com', DomainStatus.MIRROR),
        DomainEntry('sbplay3.com', DomainStatus.MIRROR),
        DomainEntry('tubesb.com', DomainStatus.MIRROR),
        DomainEntry('sbfull.com', DomainStatus.MIRROR),
        DomainEntry('sbspeed.com', DomainStatus.MIRROR),
        DomainEntry('sbhight.com', DomainStatus.MIRROR),
        DomainEntry('sbfast.com', DomainStatus.MIRROR),
        DomainEntry('sbani.pro', DomainStatus.MIRROR),
        DomainEntry('sbrity.com', DomainStatus.MIRROR),
        DomainEntry('sbthe.com', DomainStatus.MIRROR),
        DomainEntry('sblongvu.com', DomainStatus.MIRROR),
        DomainEntry('sbplay.one', DomainStatus.MIRROR),
        DomainEntry('ssbstream.net', DomainStatus.MIRROR),
        # Legacy
        DomainEntry('sbchill.com', DomainStatus.LEGACY),
        DomainEntry('sblanh.com', DomainStatus.LEGACY),
        DomainEntry('sbanh.com', DomainStatus.LEGACY),
    ],

    url_patterns=[
        r'(?:stream|embed|cloud|tube|watch|play(?:er)?)sb\d*\.(?:net|com|org)',
        r'sb(?:play|embed|video|the|hight|speed|fast|full|ani|longvu|rity|chill|lanh)\d*\.(?:com|net|org|pro|one)',
        r'ssbstream\.net',
        r'lvturbo\.com',
        r'cloudemb\.com',
    ],

    iframe_indicators=['streamsb', 'embedsb', 'watchsb', 'sbplay', 'cloudemb', 'lvturbo'],
    html_indicators=['streamsb', '/sources'],
    js_indicators=['make_play', 'download_video'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EMBED_API,
        typical_format=MediaFormat.HLS,

        challenge_types=[
            ChallengeType.OBFUSCATED_JS,
            ChallengeType.SIGNED_URL,
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /e/{video_id}.html",
            "2. Extract video ID dari URL",
            "3. Build API URL: /sources/{hex_encoded_id}",
            "4. GET API URL dengan headers khusus",
            "5. Response JSON berisi m3u8 URL",
            "6. Fetch m3u8 dengan Referer header",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/e/{vid_id}.html')",
            "# Build hex-encoded path",
            "hex_id = ''.join([f'{ord(c):02x}' for c in vid_id])",
            "api_url = f'{host}/sources{hex_id}'",
            "# ATAU pattern lain:",
            "api_url = f'{host}/sources/{vid_id}'",
            "resp = fetch(api_url, headers={'watchsb': 'sbstream', ...})",
            "m3u8 = resp.json()['stream_data']['file']",
        ],

        key_regex_patterns={
            'video_id': r'/e/([a-zA-Z0-9]+)',
            'sources_url': r'/sources/([a-zA-Z0-9]+)',
        },

        requires_referer=True,
        header_dependencies={
            'Referer': '{embed_url}',
            'watchsb': 'sbstream',
        },

        api_endpoints=[
            APIEndpoint(
                path='/sources/{video_id_hex}',
                method='GET',
                purpose='get_sources',
                required_headers={
                    'watchsb': 'sbstream',
                    'Referer': '{embed_url}',
                },
                response_format='json',
                media_key_paths=['stream_data.file'],
                note='video_id mungkin perlu di-hex-encode',
            ),
        ],

        failure_modes=[
            "Domain rotate SANGAT sering",
            "Header 'watchsb: sbstream' kadang berubah",
            "Hex encoding scheme bisa berubah",
            "Beberapa domain butuh User-Agent spesifik",
        ],
    ),

    tier=ServiceTier.TIER_1,
    reliability=ServiceReliability.LOW,
    last_verified='2024-11-01',
    notes='Domain paling banyak variant. Header khusus WAJIB.',
)


# ══════════════════════════════════════════════════════════
#  8. MIXDROP
# ══════════════════════════════════════════════════════════

MIXDROP = EmbedServiceSignature(
    id='mixdrop',
    display_name='MixDrop',
    family_id='',
    aliases=['mixdrp', 'mdrop'],

    domains=[
        DomainEntry('mixdrop.co', DomainStatus.PRIMARY),
        DomainEntry('mixdrop.to', DomainStatus.COMMON),
        DomainEntry('mixdrop.ch', DomainStatus.COMMON),
        DomainEntry('mixdrop.bz', DomainStatus.COMMON),
        DomainEntry('mixdrop.sx', DomainStatus.MIRROR),
        DomainEntry('mixdrop.ag', DomainStatus.MIRROR),
        DomainEntry('mixdrop.club', DomainStatus.MIRROR),
        DomainEntry('mixdrop.gl', DomainStatus.MIRROR),
        DomainEntry('mixdrp.co', DomainStatus.MIRROR),
        DomainEntry('mixdrp.to', DomainStatus.MIRROR),
        DomainEntry('mixdrop.nu', DomainStatus.UNCERTAIN),
        DomainEntry('mixdrop.ps', DomainStatus.UNCERTAIN),
    ],

    url_patterns=[
        r'mixdr(?:o)?p\.(?:co|to|ch|bz|sx|ag|club|gl|nu|ps)',
    ],

    iframe_indicators=['mixdrop', 'mixdrp'],
    html_indicators=['mixdrop', 'MDCore'],
    js_indicators=['MDCore', 'eval(function(p,a,c,k,e,d)', 'wurl'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EVAL_JS,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.PACKED_JS,
            ChallengeType.MULTI_REDIRECT,
        ],

        flow_steps=[
            "1. Fetch embed page /e/{file_code}",
            "2. Find eval(function(p,a,c,k,e,d) block",
            "3. Unpack P.A.C.K.E.R",
            "4. Extract redirect URL (MDCore.wurl atau s2 variable)",
            "5. URL biasanya //delivery...mixdrop.../v.mp4",
            "6. Tambah https: prefix jika perlu",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/e/{file_code}')",
            "packed = regex(r'eval\\(function\\(p,a,c,k,e,d\\).+?\\)\\)', html)",
            "unpacked = packer_unpack(packed)",
            "# Cari direct URL:",
            "video_url = regex(r'MDCore\\.wurl=\"([^\"]+)\"', unpacked)",
            "# ATAU:",
            "video_url = regex(r'\\|([a-z0-9]+\\.mp4)\\|', unpacked)",
            "# Reconstruct full URL dari unpacked vars",
        ],

        key_regex_patterns={
            'packed_js': r'eval\(function\(p,a,c,k,e,d\).+?\)\)',
            'wurl': r'MDCore\.wurl="([^"]+)"',
            'redirect_url': r'window\.location\s*=\s*"([^"]+)"',
            'delivery_url': r'(//[a-z0-9.]+mixdrop[^"\'\\s]+\.mp4)',
        },

        decode_methods=['js_unpack_p_a_c_k_e_d'],

        requires_referer=True,
        header_dependencies={
            'Referer': '{embed_url}',
        },

        failure_modes=[
            "Packed JS structure bisa berubah",
            "Video URL bisa mulai dengan // (protocol-relative)",
            "Delivery domain berubah-ubah",
        ],
    ),

    tier=ServiceTier.TIER_1,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes='P.A.C.K.E.R unpack. URL bisa protocol-relative.',
)


# ══════════════════════════════════════════════════════════
#  9. STREAMWISH
# ══════════════════════════════════════════════════════════

STREAMWISH = EmbedServiceSignature(
    id='streamwish',
    display_name='StreamWish',
    family_id='',
    aliases=['swish', 'wishembed', 'embedwish', 'strwish'],

    domains=[
        DomainEntry('streamwish.to', DomainStatus.PRIMARY),
        DomainEntry('streamwish.com', DomainStatus.COMMON),
        DomainEntry('wishembed.pro', DomainStatus.COMMON),
        DomainEntry('embedwish.com', DomainStatus.COMMON),
        DomainEntry('strwish.com', DomainStatus.COMMON),
        DomainEntry('awish.pro', DomainStatus.MIRROR),
        DomainEntry('dwish.pro', DomainStatus.MIRROR),
        DomainEntry('mwish.pro', DomainStatus.MIRROR),
        DomainEntry('swish.pro', DomainStatus.MIRROR),
        DomainEntry('cwish.pro', DomainStatus.MIRROR),
        DomainEntry('ewish.pro', DomainStatus.UNCERTAIN),
        DomainEntry('fwish.pro', DomainStatus.UNCERTAIN),
    ],

    url_patterns=[
        r'(?:stream|embed|str|[a-z])wish\.(?:to|com|pro)',
    ],

    iframe_indicators=['streamwish', 'wishembed', 'embedwish', 'strwish'],
    html_indicators=['streamwish'],
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
            "3. Unpack P.A.C.K.E.R",
            "4. Extract m3u8 URL dari unpacked JS",
            "5. Fetch m3u8 dengan Referer",
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
        },

        decode_methods=['js_unpack_p_a_c_k_e_d'],
        requires_referer=True,
        header_dependencies={'Referer': '{embed_url}'},

        failure_modes=[
            "Mirip FileMoon — P.A.C.K.E.R unpack",
            "Domain [a-z]wish.pro bisa rotate",
        ],
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes='Pattern mirip FileMoon. P.A.C.K.E.R standard.',
)


# ══════════════════════════════════════════════════════════
#  10. VIDOZA
# ══════════════════════════════════════════════════════════

VIDOZA = EmbedServiceSignature(
    id='vidoza',
    display_name='Vidoza',
    family_id='',
    aliases=[],

    domains=[
        DomainEntry('vidoza.net', DomainStatus.PRIMARY),
        DomainEntry('vidoza.org', DomainStatus.MIRROR),
        DomainEntry('vidoza.co', DomainStatus.MIRROR),
    ],

    url_patterns=[r'vidoza\.(?:net|org|co)'],

    iframe_indicators=['vidoza'],
    html_indicators=['vidoza'],
    js_indicators=['sourcesCode', 'playerInstance'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.DIRECT,
        typical_format=MediaFormat.MP4,

        challenge_types=[ChallengeType.NONE],

        flow_steps=[
            "1. Fetch embed page /embed-{video_id}.html",
            "2. Find <source src='...'> tag di HTML",
            "3. Extract direct MP4 URL",
            "4. Download MP4 langsung",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/embed-{vid_id}.html')",
            "mp4_url = regex(r'<source src=\"([^\"]+)\" type=\"video/mp4\"', html)",
            "# ATAU:",
            "mp4_url = regex(r'sourcesCode:.+?src:\\s*\"([^\"]+)\"', html)",
        ],

        key_regex_patterns={
            'source_tag': r'<source src="([^"]+)" type="video/mp4"',
            'sources_code': r'sourcesCode:.+?src:\s*"([^"]+)"',
        },

        requires_referer=True,
        header_dependencies={'Referer': '{embed_url}'},

        failure_modes=[
            "Sangat simpel, jarang gagal",
        ],
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.HIGH,
    last_verified='2024-12-01',
    notes='Paling simpel — direct source tag.',
)


# ══════════════════════════════════════════════════════════
#  11. UPSTREAM (CLEAN — tanpa Uqload)
# ══════════════════════════════════════════════════════════

UPSTREAM = EmbedServiceSignature(
    id='upstream',
    display_name='Upstream',
    family_id='',
    aliases=['upstr'],
    # REMOVED: uqload.com, uqload.to → Part C sebagai service terpisah

    domains=[
        DomainEntry('upstream.to', DomainStatus.PRIMARY),
    ],

    url_patterns=[r'upstream\.to'],

    iframe_indicators=['upstream.to'],
    html_indicators=['upstream'],
    js_indicators=['eval(function(p,a,c,k,e,d)'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.EVAL_JS,
        typical_format=MediaFormat.MP4,

        challenge_types=[ChallengeType.PACKED_JS],

        flow_steps=[
            "1. Fetch embed page /embed-{file_code}",
            "2. Find eval(function(p,a,c,k,e,d) block",
            "3. Unpack P.A.C.K.E.R",
            "4. Extract direct MP4 URL",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/embed-{file_code}')",
            "packed = regex(r'eval\\(function\\(p,a,c,k,e,d\\).+?\\)\\)', html)",
            "unpacked = packer_unpack(packed)",
            "mp4_url = regex(r'file:\\s*\"([^\"]+\\.mp4[^\"]*)\"', unpacked)",
        ],

        key_regex_patterns={
            'packed_js': r'eval\(function\(p,a,c,k,e,d\).+?\)\)',
            'mp4_url': r'file:\s*"([^"]+\.mp4[^"]*)"',
        },

        decode_methods=['js_unpack_p_a_c_k_e_d'],
        requires_referer=True,
        header_dependencies={'Referer': '{embed_url}'},

        failure_modes=["Standard P.A.C.K.E.R"],
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.HIGH,
    last_verified='2024-12-01',
)


# ══════════════════════════════════════════════════════════
#  12. OK.RU
# ══════════════════════════════════════════════════════════

OKRU = EmbedServiceSignature(
    id='okru',
    display_name='OK.ru',
    family_id='',
    aliases=['odnoklassniki', 'odkl'],

    domains=[
        DomainEntry('ok.ru', DomainStatus.PRIMARY),
        DomainEntry('odnoklassniki.ru', DomainStatus.LEGACY),
    ],

    url_patterns=[
        r'ok\.ru/(?:video|videoembed|live)',
    ],

    iframe_indicators=['ok.ru/videoembed', 'ok.ru/video'],
    html_indicators=['ok.ru'],
    js_indicators=['okVideoPlayer', 'OKVideo'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.API,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /videoembed/{video_id}",
            "2. Find flashvars atau data-options di HTML",
            "3. Parse JSON dari flashvars",
            "4. Extract video URLs per quality (mobile, lowest, low, sd, hd)",
            "5. URLs biasanya direct MP4",
        ],

        pseudo_flow=[
            "html = fetch(f'https://ok.ru/videoembed/{vid_id}')",
            "flashvars_match = regex(r'data-options=\"([^\"]+)\"', html)",
            "# ATAU:",
            "flashvars_match = regex(r'flashvars.*?\"([^\"]+)\"', html)",
            "options = json.loads(html_unescape(flashvars_match))",
            "metadata = json.loads(options.get('flashvars', {}).get('metadata', '{}'))",
            "videos = metadata.get('videos', [])",
            "# Each video: {'name': 'hd', 'url': 'https://...mp4'}",
        ],

        key_regex_patterns={
            'data_options': r'data-options="([^"]+)"',
            'flashvars': r'flashvars["\s]*[=:]\s*"([^"]+)"',
            'metadata': r'"metadata"\s*:\s*"([^"]+)"',
        },

        requires_referer=False,
        requires_cookies=True,
        cookie_dependencies=['_clientId'],

        failure_modes=[
            "Geo-restricted ke beberapa negara",
            "Cookie _clientId mungkin diperlukan",
            "Video private butuh login",
        ],
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes='Multi-quality. Geo-restriction mungkin berlaku.',
)


# ══════════════════════════════════════════════════════════
#  13. SENDVID
# ══════════════════════════════════════════════════════════

SENDVID = EmbedServiceSignature(
    id='sendvid',
    display_name='SendVid',
    family_id='',
    aliases=[],

    domains=[
        DomainEntry('sendvid.com', DomainStatus.PRIMARY),
    ],

    url_patterns=[r'sendvid\.com'],

    iframe_indicators=['sendvid.com'],
    html_indicators=['sendvid'],
    js_indicators=['video_source', 'sendvid'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.DIRECT,
        typical_format=MediaFormat.MP4,
        challenge_types=[ChallengeType.NONE],

        flow_steps=[
            "1. Fetch embed page /embed/{video_id}",
            "2. Find <source> tag atau video_source variable",
            "3. Extract direct MP4 URL",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/embed/{vid_id}')",
            "mp4_url = regex(r'<source src=\"([^\"]+)\" type=\"video/mp4\"', html)",
            "# ATAU:",
            "mp4_url = regex(r'video_source\\s*=\\s*\"([^\"]+)\"', html)",
        ],

        key_regex_patterns={
            'source_tag': r'<source src="([^"]+)" type="video/mp4"',
            'video_source': r'video_source\s*=\s*"([^"]+)"',
        },

        requires_referer=False,
        failure_modes=["Sangat simpel"],
    ),

    tier=ServiceTier.TIER_3,
    reliability=ServiceReliability.HIGH,
    last_verified='2024-12-01',
)


# ══════════════════════════════════════════════════════════
#  14. FEMBED / FCDN
# ══════════════════════════════════════════════════════════

FEMBED = EmbedServiceSignature(
    id='fembed',
    display_name='Fembed',
    family_id='fembed_family',
    family_relation=FamilyRelation.PARENT,
    aliases=['fcdn', 'femax', 'feurl', 'fplayer', 'embedsito'],

    domains=[
        DomainEntry('fembed.com', DomainStatus.PRIMARY),
        DomainEntry('femax20.com', DomainStatus.COMMON),
        DomainEntry('fcdn.stream', DomainStatus.COMMON),
        DomainEntry('feurl.com', DomainStatus.COMMON),
        DomainEntry('embedsito.com', DomainStatus.MIRROR),
        DomainEntry('fplayer.info', DomainStatus.MIRROR),
        DomainEntry('dutrag.com', DomainStatus.MIRROR),
        DomainEntry('diasfem.com', DomainStatus.MIRROR),
        DomainEntry('suzihaza.com', DomainStatus.MIRROR),
        DomainEntry('vanfem.com', DomainStatus.MIRROR),
        DomainEntry('fembed-hd.com', DomainStatus.LEGACY),
        DomainEntry('fem.tohols.com', DomainStatus.LEGACY),
    ],

    url_patterns=[
        r'(?:fembed|femax\d*|fcdn|feurl|embedsito|fplayer)\.(?:com|stream|info)',
        r'(?:dutrag|diasfem|suzihaza|vanfem)\.com',
    ],

    iframe_indicators=['fembed', 'femax', 'fcdn', 'feurl'],
    html_indicators=['fembed', 'fplayer'],
    js_indicators=['/api/source/'],

    extraction=ExtractionProfile(
        method=ExtractionMethod.API,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Extract video ID dari embed URL /v/{video_id}",
            "2. POST /api/source/{video_id}",
            "3. Response JSON berisi list sources per quality",
            "4. Pilih quality tertinggi",
            "5. Download MP4 dari URL di response",
        ],

        pseudo_flow=[
            "vid_id = regex(r'/v/([a-zA-Z0-9]+)', embed_url)",
            "api_url = f'{host}/api/source/{vid_id}'",
            "resp = post(api_url, data={}, referer=embed_url)",
            "sources = resp.json()['data']",
            "# sources = [{'file': 'https://...mp4', 'label': '720p', 'type': 'mp4'}, ...]",
            "best = max(sources, key=lambda s: int(s['label'].replace('p','')))",
            "video_url = best['file']",
        ],

        key_regex_patterns={
            'video_id': r'/v/([a-zA-Z0-9]+)',
        },

        requires_referer=True,
        header_dependencies={'Referer': '{embed_url}'},

        api_endpoints=[
            APIEndpoint(
                path='/api/source/{video_id}',
                method='POST',
                purpose='get_sources',
                response_format='json',
                media_key_paths=['data[].file'],
                note='POST with empty body, returns multi-quality sources',
            ),
        ],

        failure_modes=[
            "Banyak domain mati, sering rotate",
            "API bisa return empty data jika video expired",
            "Beberapa domain pakai anti-bot protection",
        ],
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.LOW,
    last_verified='2024-10-01',
    notes='Banyak domain sudah mati. Service mungkin declining.',
)


# ══════════════════════════════════════════════════════════
#  SERVICE FAMILIES
# ══════════════════════════════════════════════════════════

VIDCLOUD_FAMILY = ServiceFamily(
    family_id='vidcloud_family',
    display_name='VidCloud Family',
    members=[
        FamilyMember('vidcloud', FamilyRelation.PARENT),
        FamilyMember('vidplay', FamilyRelation.SIBLING,
                     note='Separate service, same ecosystem'),
        # megacloud akan ditambahkan di Part C/D
    ],
    shared_extraction_method=ExtractionMethod.EMBED_API,
    note='Ecosystem streaming paling besar. Shared API pattern.',
)

FILEMOON_FAMILY = ServiceFamily(
    family_id='filemoon_family',
    display_name='FileMoon Family',
    members=[
        FamilyMember('filemoon', FamilyRelation.PARENT),
        # filelions → Part C sebagai SIBLING
    ],
    shared_extraction_method=ExtractionMethod.EVAL_JS,
    shared_api_pattern='P.A.C.K.E.R unpack',
    note='FileLions adalah SIBLING, bukan alias. Entry terpisah di Part C.',
)

DOOD_FAMILY = ServiceFamily(
    family_id='dood_family',
    display_name='DoodStream Family',
    members=[
        FamilyMember('doodstream', FamilyRelation.PARENT),
    ],
    shared_extraction_method=ExtractionMethod.API,
    note='Semua domain adalah alias dari doodstream.',
)

STREAMSB_FAMILY = ServiceFamily(
    family_id='streamsb_family',
    display_name='StreamSB Family',
    members=[
        FamilyMember('streamsb', FamilyRelation.PARENT),
    ],
    shared_extraction_method=ExtractionMethod.EMBED_API,
    note='Semua domain adalah alias dari StreamSB.',
)

STREAMTAPE_FAMILY = ServiceFamily(
    family_id='streamtape_family',
    display_name='StreamTape Family',
    members=[
        FamilyMember('streamtape', FamilyRelation.PARENT),
    ],
)

FEMBED_FAMILY = ServiceFamily(
    family_id='fembed_family',
    display_name='Fembed Family',
    members=[
        FamilyMember('fembed', FamilyRelation.PARENT),
    ],
    shared_extraction_method=ExtractionMethod.API,
    shared_api_pattern='/api/source/',
)


# ══════════════════════════════════════════════════════════
#  COLLECTIONS (for easy registration)
# ══════════════════════════════════════════════════════════

ALL_SERVICES_TIER1 = [
    VIDCLOUD,
    VIDPLAY,
    FILEMOON,
    STREAMTAPE,
    DOODSTREAM,
    MP4UPLOAD,
    STREAMSB,
    MIXDROP,
    STREAMWISH,
    VIDOZA,
    UPSTREAM,
    OKRU,
    SENDVID,
    FEMBED,
]

ALL_FAMILIES_TIER1 = [
    VIDCLOUD_FAMILY,
    FILEMOON_FAMILY,
    DOOD_FAMILY,
    STREAMSB_FAMILY,
    STREAMTAPE_FAMILY,
    FEMBED_FAMILY,
]


# ══════════════════════════════════════════════════════════
#  REGISTRATION FUNCTION
# ══════════════════════════════════════════════════════════

def register_tier1_services(registry: EmbedServiceRegistry = None):
    """
    Register semua Tier 1 & 2 services ke registry.
    Dipanggil dari init_registry().
    """
    if registry is None:
        registry = EmbedServiceRegistry.get_instance()

    # Register families first
    for family in ALL_FAMILIES_TIER1:
        registry.register_family(family)

    # Register services
    registry.register_services(ALL_SERVICES_TIER1)

    return registry