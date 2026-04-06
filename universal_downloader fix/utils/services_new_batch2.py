"""
services_new_batch2.py — New services batch 2 (medium priority)

6 services yang MISSING, dengan normalized structure:

  1. turbovid     — TurboVid / TurboVideo
  2. videy        — Videy
  3. vk_video     — VK Video
  4. google_video — Blogger / Googlevideo / GDrive (google_family)
  5. sibnet       — Sibnet
  6. netu         — Netu / Waaw / HQQ family (COMPLEX)

Each service includes:
  • Identity + labels (inline)
  • Active domains (primary + common)
  • Detection patterns
  • Extraction profile (Level 3 for complex)
  • Appendix domains (mirror/legacy/dead/uncertain)
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
    LabelMapping,
    LabelVariant,
)


# ══════════════════════════════════════════════════════════
#  1. TURBOVID / TURBOVIDEO
# ══════════════════════════════════════════════════════════
#
#  Identity
#  ────────
#  id:           turbovid
#  display_name: TurboVid
#  family:       (none)
#  aliases:      turbovideo, turbo
#  labels:       TV, TB, TurboVid, Turbo
#
# ══════════════════════════════════════════════════════════

TURBOVID = EmbedServiceSignature(
    id='turbovid',
    display_name='TurboVid',
    family_id='',
    family_relation=FamilyRelation.PARENT,
    aliases=['turbovideo', 'turbo'],

    # ── Active Domains (primary + common) ──
    domains=[
        DomainEntry('turbovid.com', DomainStatus.PRIMARY),
        DomainEntry('turbo.ax', DomainStatus.COMMON),
        DomainEntry('turbovid.to', DomainStatus.COMMON),
    ],

    # ── Detection ──
    url_patterns=[
        r'turbovid\.(?:com|to)',
        r'turbo\.ax',
    ],
    iframe_indicators=['turbovid', 'turbo.ax'],
    html_indicators=['turbovid'],
    js_indicators=['eval(function(p,a,c,k,e,d)', 'file:"'],

    # ── Extraction Profile ──
    extraction=ExtractionProfile(
        method=ExtractionMethod.EVAL_JS,
        typical_format=MediaFormat.HLS,

        challenge_types=[
            ChallengeType.PACKED_JS,
            ChallengeType.ANTI_HOTLINK,
        ],

        # Step-by-step
        flow_steps=[
            "1. Fetch embed page /e/{file_code} atau /embed/{file_code}",
            "2. Find eval(function(p,a,c,k,e,d) block",
            "3. Unpack P.A.C.K.E.R",
            "4. Extract m3u8 URL dari unpacked JS",
            "5. Fetch m3u8 dengan Referer",
        ],

        # Pseudo-code
        pseudo_flow=[
            "html = fetch(f'{host}/e/{file_code}')",
            "packed = regex(r'eval\\(function\\(p,a,c,k,e,d\\).+?\\)\\)', html)",
            "unpacked = packer_unpack(packed)",
            "m3u8_url = regex(r'file:\\s*\"([^\"]+\\.m3u8[^\"]*)\"', unpacked)",
        ],

        # Implementable hints
        key_regex_patterns={
            'packed_js': r'eval\(function\(p,a,c,k,e,d\).+?\)\)',
            'm3u8_url': r'file:\s*"([^"]+\.m3u8[^"]*)"',
        },
        decode_methods=['js_unpack_p_a_c_k_e_d'],
        requires_referer=True,
        header_dependencies={'Referer': '{embed_url}'},

        failure_modes=[
            "Standard P.A.C.K.E.R pattern",
            "Mirip FileMoon family extraction",
        ],
    ),

    tier=ServiceTier.TIER_3,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-11-01',
    notes='P.A.C.K.E.R based. Same pattern as FileMoon family.',
)

TURBOVID_LABELS = LabelVariant(
    service_id='turbovid',
    labels=['TV', 'TB', 'TurboVid', 'TurboVideo', 'Turbo'],
    partial_match=True,
)

# ── Appendix Domains ──
TURBOVID_APPENDIX_DOMAINS = [
    DomainEntry('turbovid.net', DomainStatus.UNCERTAIN),
    DomainEntry('turbovideo.cc', DomainStatus.UNCERTAIN),
    DomainEntry('turbovideo.to', DomainStatus.DEAD,
                note='Redirects to turbovid.com'),
]


# ══════════════════════════════════════════════════════════
#  2. VIDEY
# ══════════════════════════════════════════════════════════
#
#  Identity
#  ────────
#  id:           videy
#  display_name: Videy
#  family:       (none)
#  aliases:      (none)
#  labels:       Videy, VY
#
# ══════════════════════════════════════════════════════════

VIDEY = EmbedServiceSignature(
    id='videy',
    display_name='Videy',
    family_id='',
    family_relation=FamilyRelation.PARENT,
    aliases=[],

    # ── Active Domains ──
    domains=[
        DomainEntry('videy.co', DomainStatus.PRIMARY),
        DomainEntry('videy.to', DomainStatus.COMMON),
    ],

    # ── Detection ──
    url_patterns=[r'videy\.(?:co|to)'],
    iframe_indicators=['videy.co', 'videy.to'],
    html_indicators=['videy'],
    js_indicators=['sources:', 'playerInstance'],

    # ── Extraction Profile ──
    extraction=ExtractionProfile(
        method=ExtractionMethod.DIRECT,
        typical_format=MediaFormat.MP4,

        challenge_types=[ChallengeType.ANTI_HOTLINK],

        flow_steps=[
            "1. Fetch embed page /e/{video_id}",
            "2. Find <source> tag atau sources array",
            "3. Extract direct MP4 URL",
            "4. Download MP4 dengan Referer",
        ],

        pseudo_flow=[
            "html = fetch(f'{host}/e/{vid_id}')",
            "mp4_url = regex(r'<source src=\"([^\"]+)\" type=\"video/mp4\"', html)",
            "# Fallback:",
            "mp4_url = regex(r'sources:\\s*\\[\\{\\s*src:\\s*\"([^\"]+)\"', html)",
        ],

        key_regex_patterns={
            'source_tag': r'<source src="([^"]+)" type="video/mp4"',
            'sources_src': r'sources:\s*\[\{\s*src:\s*"([^"]+)"',
        },
        requires_referer=True,
        header_dependencies={'Referer': '{embed_url}'},

        failure_modes=["Simpel. Direct source extraction."],
    ),

    tier=ServiceTier.TIER_3,
    reliability=ServiceReliability.HIGH,
    last_verified='2024-11-01',
    notes='Very simple — direct source tag. Low priority service.',
)

VIDEY_LABELS = LabelVariant(
    service_id='videy',
    labels=['Videy', 'VY'],
    partial_match=True,
)

VIDEY_APPENDIX_DOMAINS = [
    DomainEntry('videy.net', DomainStatus.UNCERTAIN),
]


# ══════════════════════════════════════════════════════════
#  3. VK VIDEO
# ══════════════════════════════════════════════════════════
#
#  Identity
#  ────────
#  id:           vk_video
#  display_name: VK Video
#  family:       (none — OK.ru is separate)
#  aliases:      vkontakte, vk
#  labels:       VK, VKontakte, VK Video
#
#  EXTRACTION: Level 3 (moderate complexity)
#
# ══════════════════════════════════════════════════════════

VK_VIDEO = EmbedServiceSignature(
    id='vk_video',
    display_name='VK Video',
    family_id='',
    family_relation=FamilyRelation.PARENT,
    aliases=['vkontakte', 'vk'],

    # ── Active Domains ──
    domains=[
        DomainEntry('vk.com', DomainStatus.PRIMARY),
        DomainEntry('vkvideo.ru', DomainStatus.COMMON,
                    note='New dedicated video domain'),
        DomainEntry('vk.ru', DomainStatus.COMMON),
    ],

    # ── Detection ──
    url_patterns=[
        r'vk\.com/video_ext\.php',
        r'vk\.com/video[-_]',
        r'vkvideo\.ru',
        r'vk\.ru/video',
    ],
    iframe_indicators=['vk.com/video_ext', 'vkvideo.ru'],
    html_indicators=['vk.com/video', 'vkvideo'],
    js_indicators=['mvData', 'player.js', 'videoPlayer', 'al_video.php'],

    # ── Extraction Profile (Level 3) ──
    extraction=ExtractionProfile(
        method=ExtractionMethod.API,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.ANTI_HOTLINK,
            ChallengeType.SIGNED_URL,
            ChallengeType.BROWSER_CHECK,
        ],

        # ── Step-by-step ──
        flow_steps=[
            "1. Fetch embed page /video_ext.php?oid={owner_id}&id={video_id}&hash={hash}",
            "2. Extract 'mvData' atau 'playerParams' JSON object dari HTML",
            "3. JSON berisi URL per quality: url240, url360, url480, url720, url1080",
            "4. URL sudah signed — langsung downloadable",
            "5. Untuk private videos: butuh login/cookie",
            "6. ALTERNATIF: API al_video.php (butuh auth)",
        ],

        # ── Pseudo-code ──
        pseudo_flow=[
            "# Method 1: Embed page (public videos)",
            "embed_url = f'https://vk.com/video_ext.php?oid={oid}&id={vid}&hash={hash}'",
            "html = fetch(embed_url)",
            "",
            "# Extract player data",
            "mv_data = regex(r'var\\s+playerParams\\s*=\\s*(\\{.+?\\});', html)",
            "# ATAU:",
            "mv_data = regex(r'\"mvData\"\\s*:\\s*(\\{.+?\\})', html)",
            "",
            "data = json.loads(mv_data)",
            "params = data.get('params', [{}])[0]",
            "",
            "# Quality URLs",
            "qualities = {}",
            "for q in ['2160', '1440', '1080', '720', '480', '360', '240']:",
            "    url = params.get(f'url{q}')",
            "    if url:",
            "        qualities[f'{q}p'] = url",
            "",
            "# Best quality",
            "best_url = list(qualities.values())[0]  # highest first",
            "",
            "# Method 2: al_video.php API (authenticated)",
            "# POST https://vk.com/al_video.php",
            "# act=show&al=1&video={oid}_{vid}",
            "# Requires valid VK cookies",
        ],

        # ── Implementable Hints ──
        key_regex_patterns={
            'player_params': r'var\s+playerParams\s*=\s*(\{.+?\});',
            'mv_data': r'"mvData"\s*:\s*(\{.+?\})',
            'url_quality': r'"url(\d+)"\s*:\s*"([^"]+)"',
            'video_id': r'video_ext\.php\?oid=([-\d]+)&id=(\d+)&hash=([a-f0-9]+)',
            'embed_hash': r'hash=([a-f0-9]+)',
        },

        requires_referer=True,
        requires_cookies=True,  # For private videos
        cookie_dependencies=['remixsid'],  # VK session cookie

        header_dependencies={
            'Referer': 'https://vk.com/',
        },

        api_endpoints=[
            APIEndpoint(
                path='/al_video.php',
                method='POST',
                purpose='get_video_data',
                content_type='application/x-www-form-urlencoded',
                required_params={
                    'act': 'show',
                    'al': '1',
                    'video': '{oid}_{vid}',
                },
                response_format='json',
                media_key_paths=['payload[1][4].urls'],
                note='Requires VK session (remixsid cookie)',
            ),
        ],

        token_expiry_seconds=86400,  # ~24 jam

        fallback_strategy=(
            "Public videos: embed page parsing (no auth needed). "
            "Private videos: butuh VK login cookie (remixsid). "
            "Jika embed parsing gagal, coba al_video.php API."
        ),

        failure_modes=[
            "Private videos butuh VK login",
            "Geo-restriction (beberapa video blocked di luar Russia)",
            "Signed URLs tapi long expiry (~24h)",
            "vkvideo.ru = domain baru, mungkin behavior berbeda",
            "Embed hash wajib — tanpa hash, 403",
        ],

        requires_browser=False,
        browser_note="Browser hanya perlu untuk private videos yang butuh login",
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes=(
        'Multi-quality (240p-2160p). Public videos mudah. '
        'Private videos butuh VK login cookie. '
        'vkvideo.ru = new dedicated video platform dari VK.'
    ),
)

VK_VIDEO_LABELS = LabelVariant(
    service_id='vk_video',
    labels=['VK', 'VKontakte', 'VK Video', 'VKvid'],
    partial_match=True,
)

VK_VIDEO_APPENDIX_DOMAINS = [
    DomainEntry('m.vk.com', DomainStatus.LEGACY, note='Mobile VK'),
    DomainEntry('vkontakte.ru', DomainStatus.DEAD, note='Old brand name'),
]


# ══════════════════════════════════════════════════════════
#  4. GOOGLE VIDEO FAMILY
#     (Blogger / Googlevideo / Google Drive)
# ══════════════════════════════════════════════════════════
#
#  Identity
#  ────────
#  id:           google_video
#  display_name: Google Video
#  family:       google_family
#  aliases:      blogger, blogspot, googlevideo, gdrive
#  labels:       GDrive, Google, GV, Blogger
#
#  EXTRACTION: Level 3 (3 sub-variants)
#
# ══════════════════════════════════════════════════════════

GOOGLE_VIDEO = EmbedServiceSignature(
    id='google_video',
    display_name='Google Video',
    family_id='google_family',
    family_relation=FamilyRelation.PARENT,
    aliases=['blogger', 'blogspot', 'googlevideo', 'gdrive'],

    # ── Active Domains ──
    domains=[
        # Blogger / Googlevideo (delivery)
        DomainEntry('redirector.googlevideo.com', DomainStatus.PRIMARY,
                    note='Blogger video delivery domain'),
        DomainEntry('blogger.com', DomainStatus.PRIMARY,
                    note='Blogger embeds'),
        DomainEntry('blogspot.com', DomainStatus.PRIMARY,
                    note='Blogger embeds alt'),
        DomainEntry('www.blogger.com', DomainStatus.COMMON),

        # Google Drive
        DomainEntry('drive.google.com', DomainStatus.PRIMARY,
                    note='Google Drive video sharing'),
        DomainEntry('docs.google.com', DomainStatus.COMMON,
                    note='Docs video embed'),
        DomainEntry('drive.usercontent.google.com', DomainStatus.COMMON,
                    note='New Drive download domain'),

        # General Google video
        DomainEntry('video.google.com', DomainStatus.COMMON),
    ],

    # ── Detection ──
    url_patterns=[
        r'redirector\.googlevideo\.com',
        r'r\d+---sn-[a-z0-9]+\.googlevideo\.com',
        r'blogger\.com/video\.g\?token=',
        r'drive\.google\.com/file/d/',
        r'drive\.google\.com/uc\?',
        r'docs\.google\.com/file/d/',
        r'lh3\.googleusercontent\.com',
        r'video\.google\.com',
    ],

    iframe_indicators=[
        'blogger.com/video', 'drive.google.com',
        'docs.google.com', 'googlevideo.com',
    ],
    html_indicators=['googlevideo', 'blogger.com/video', 'drive.google.com/file'],
    js_indicators=['googlevideo', 'videoplayback', 'drive.google.com'],

    # ── Extraction Profile (Level 3) ──
    extraction=ExtractionProfile(
        method=ExtractionMethod.API,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.SIGNED_URL,
            ChallengeType.ANTI_HOTLINK,
            ChallengeType.TOKEN_ROTATION,
        ],

        # ── Step-by-step (3 sub-variants) ──
        flow_steps=[
            "=== VARIANT A: Blogger/Googlevideo ===",
            "1. Source URL = blogger.com/video.g?token={token}",
            "2. Fetch → 302 redirect ke redirector.googlevideo.com/videoplayback?...",
            "3. Follow redirect → final URL = r{N}---sn-{X}.googlevideo.com/videoplayback?...",
            "4. URL berisi params: itag, source, expire, sig, etc",
            "5. Download MP4 langsung dari final URL",
            "",
            "=== VARIANT B: Google Drive ===",
            "1. Source URL = drive.google.com/file/d/{FILE_ID}/view",
            "2. GET https://drive.google.com/uc?id={FILE_ID}&export=download",
            "3. Jika file besar: muncul confirm page",
            "4. GET confirm URL dengan cookie yang didapat",
            "5. ATAU: extract videoplayback URL dari embed page",
            "",
            "=== VARIANT C: Direct Googlevideo ===",
            "1. URL langsung ke *.googlevideo.com/videoplayback?...",
            "2. Download langsung — sudah final URL",
            "3. Perhatikan expire param di URL",
        ],

        # ── Pseudo-code ──
        pseudo_flow=[
            "# ── Variant A: Blogger ──",
            "blogger_url = 'https://www.blogger.com/video.g?token={token}'",
            "resp = fetch(blogger_url, allow_redirects=False)",
            "redirect_url = resp.headers['Location']",
            "# redirect_url = https://redirector.googlevideo.com/videoplayback?...",
            "resp2 = fetch(redirect_url, allow_redirects=False)",
            "final_url = resp2.headers['Location']",
            "# final_url = https://rN---sn-XXX.googlevideo.com/videoplayback?...",
            "# Download langsung",
            "",
            "# ── Variant B: Google Drive ──",
            "file_id = regex(r'/file/d/([a-zA-Z0-9_-]+)', drive_url)",
            "# Try direct download",
            "dl_url = f'https://drive.google.com/uc?id={file_id}&export=download'",
            "resp = fetch(dl_url, allow_redirects=True)",
            "if 'confirm' in resp.url or 'download_warning' in resp.text:",
            "    # Large file — need confirm token",
            "    confirm_token = regex(r'confirm=([a-zA-Z0-9_-]+)', resp.text)",
            "    final_url = f'{dl_url}&confirm={confirm_token}'",
            "    # Use cookies from initial request",
            "    video = fetch(final_url, cookies=resp.cookies)",
            "else:",
            "    video = resp.content",
            "",
            "# ── Variant C: Direct googlevideo ──",
            "# URL sudah final, download langsung",
            "# Cek expire param",
            "expire = regex(r'expire=(\\d+)', googlevideo_url)",
            "import time",
            "if int(expire) < time.time():",
            "    print('URL expired, need fresh URL')",
        ],

        # ── Implementable Hints ──
        key_regex_patterns={
            # Blogger
            'blogger_token': r'blogger\.com/video\.g\?token=([a-zA-Z0-9_-]+)',
            'googlevideo_redirect': r'(https?://redirector\.googlevideo\.com/videoplayback\?[^\s"\']+)',
            'googlevideo_final': r'(https?://r\d+---sn-[a-z0-9.]+\.googlevideo\.com/videoplayback\?[^\s"\']+)',
            'videoplayback_params': r'videoplayback\?(.+)',

            # GDrive
            'gdrive_file_id': r'/file/d/([a-zA-Z0-9_-]+)',
            'gdrive_confirm': r'confirm=([a-zA-Z0-9_-]+)',
            'gdrive_download_warning': r'download_warning_[a-zA-Z0-9]+=([a-zA-Z0-9_-]+)',
            'gdrive_uc': r'drive\.google\.com/uc\?id=([a-zA-Z0-9_-]+)',

            # Quality (itag values)
            'itag': r'itag=(\d+)',
        },

        requires_referer=False,  # Google biasanya tidak cek referer
        requires_cookies=True,   # GDrive perlu cookies untuk large files

        header_dependencies={},

        token_expiry_seconds=21600,  # ~6 jam untuk googlevideo URLs

        fallback_strategy=(
            "Variant A (Blogger): Follow redirects → direct MP4. "
            "Variant B (GDrive): uc?export=download → confirm jika perlu. "
            "Variant C (direct): Download langsung, cek expire. "
            "Jika URL expired, perlu re-fetch dari source page."
        ),

        failure_modes=[
            "Googlevideo URLs expire (~6 jam)",
            "GDrive large files butuh confirm token + cookies",
            "GDrive rate limit: terlalu banyak download = temporary block",
            "Video bisa private/restricted",
            "itag param menentukan quality (18=360p, 22=720p, 37=1080p)",
            "Googlevideo delivery domain (rN---sn-XXX) berubah per region",
        ],

        requires_browser=False,
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.MEDIUM,
    last_verified='2024-12-01',
    notes=(
        '3 sub-variants: Blogger (redirect chain), '
        'GDrive (uc download), Direct googlevideo. '
        'Googlevideo URLs signed with expire time. '
        'GDrive butuh cookies untuk large files.'
    ),
)

GOOGLE_VIDEO_LABELS = LabelVariant(
    service_id='google_video',
    labels=['GDrive', 'Google', 'GV', 'Blogger', 'GoogleDrive', 'Drive'],
    partial_match=True,
)

GOOGLE_FAMILY = ServiceFamily(
    family_id='google_family',
    display_name='Google Video Family',
    members=[
        FamilyMember('google_video', FamilyRelation.PARENT,
                     note='Covers Blogger, GDrive, and direct Googlevideo'),
    ],
    shared_extraction_method=ExtractionMethod.API,
    note=(
        'Single service entry covering 3 sub-variants. '
        'All use Google infrastructure but different entry points. '
        'Blogger → redirect to googlevideo. '
        'GDrive → direct download with optional confirm. '
        'Googlevideo → already final URL.'
    ),
)

GOOGLE_VIDEO_APPENDIX_DOMAINS = [
    DomainEntry('lh3.googleusercontent.com', DomainStatus.MIRROR,
                note='Image/thumbnail delivery, sometimes video'),
    DomainEntry('lh4.googleusercontent.com', DomainStatus.MIRROR),
    DomainEntry('lh5.googleusercontent.com', DomainStatus.MIRROR),
    DomainEntry('youtube.googleapis.com', DomainStatus.LEGACY,
                note='Sometimes used for private video delivery'),
    DomainEntry('storage.googleapis.com', DomainStatus.UNCERTAIN,
                note='GCS, not always video-related'),
]


# ══════════════════════════════════════════════════════════
#  5. SIBNET
# ══════════════════════════════════════════════════════════
#
#  Identity
#  ────────
#  id:           sibnet
#  display_name: Sibnet
#  family:       (none)
#  aliases:      (none)
#  labels:       Sibnet, SB (AMBIGUOUS — juga bisa StreamSB)
#
# ══════════════════════════════════════════════════════════

SIBNET = EmbedServiceSignature(
    id='sibnet',
    display_name='Sibnet',
    family_id='',
    family_relation=FamilyRelation.PARENT,
    aliases=[],

    # ── Active Domains ──
    domains=[
        DomainEntry('video.sibnet.ru', DomainStatus.PRIMARY),
        DomainEntry('sibnet.ru', DomainStatus.PRIMARY),
    ],

    # ── Detection ──
    url_patterns=[
        r'(?:video\.)?sibnet\.ru',
    ],
    iframe_indicators=['sibnet.ru', 'video.sibnet.ru'],
    html_indicators=['sibnet.ru/video'],
    js_indicators=['player.src', 'sibnet'],

    # ── Extraction Profile ──
    extraction=ExtractionProfile(
        method=ExtractionMethod.DIRECT,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.ANTI_HOTLINK,
        ],

        flow_steps=[
            "1. Fetch embed page /shell.php?videoid={id}",
            "2. Find <source> tag atau player.src assignment di JS",
            "3. URL biasanya /v/{filename}.mp4",
            "4. Build full URL: https://video.sibnet.ru/v/{filename}.mp4",
            "5. Download dengan Referer header",
        ],

        pseudo_flow=[
            "embed_url = f'https://video.sibnet.ru/shell.php?videoid={vid_id}'",
            "html = fetch(embed_url)",
            "",
            "# Method 1: source tag",
            "mp4_path = regex(r'<source src=\"([^\"]+\\.mp4)\"', html)",
            "",
            "# Method 2: JS player.src",
            "mp4_path = regex(r'player\\.src\\(\\{src:\\s*\"([^\"]+\\.mp4)\"', html)",
            "",
            "# Method 3: src= in JS",
            "mp4_path = regex(r'src:\\s*\"([^\"]+\\.mp4)\"', html)",
            "",
            "# Build full URL",
            "if mp4_path.startswith('/'):",
            "    mp4_url = f'https://video.sibnet.ru{mp4_path}'",
            "else:",
            "    mp4_url = mp4_path",
        ],

        key_regex_patterns={
            'source_tag': r'<source src="([^"]+\.mp4)"',
            'player_src': r'player\.src\(\{src:\s*"([^"]+\.mp4)"',
            'src_js': r'src:\s*"([^"]+\.mp4)"',
            'video_path': r'/v/[a-zA-Z0-9_-]+\.mp4',
        },

        requires_referer=True,
        header_dependencies={
            'Referer': 'https://video.sibnet.ru/',
        },

        failure_modes=[
            "Russian site — bisa slow dari luar Russia",
            "Referer WAJIB dari sibnet.ru",
            "Path bisa relative, perlu prefix domain",
        ],
    ),

    tier=ServiceTier.TIER_3,
    reliability=ServiceReliability.HIGH,
    last_verified='2024-12-01',
    notes=(
        'Russian video hosting. Simple direct extraction. '
        'Popular di anime/movie sites yang target Russian audience.'
    ),
)

SIBNET_LABELS = LabelVariant(
    service_id='sibnet',
    labels=['Sibnet'],
    partial_match=True,
)

# NOTE: "SB" label AMBIGUOUS — bisa Sibnet atau StreamSB
# Ini akan di-handle di Part E dengan ambiguity flag

SIBNET_APPENDIX_DOMAINS = []  # Tidak ada domain tambahan


# ══════════════════════════════════════════════════════════
#  6. NETU / WAAW / HQQ FAMILY
# ══════════════════════════════════════════════════════════
#
#  Identity
#  ────────
#  id:           netu
#  display_name: Netu
#  family:       netu_family
#  aliases:      waaw, hqq, streamhqq, goohost
#  labels:       Netu, HQQ, Waaw, NET
#
#  EXTRACTION: Level 3 (COMPLEX — eval chain + custom crypto)
#
#  Ini salah satu service paling kompleks:
#  • 20+ domain variants
#  • Eval JS chain 3-5 layers
#  • Custom token generation
#  • Frequent domain rotation
#  • Multiple embed page formats
#
# ══════════════════════════════════════════════════════════

NETU = EmbedServiceSignature(
    id='netu',
    display_name='Netu',
    family_id='netu_family',
    family_relation=FamilyRelation.PARENT,
    aliases=['waaw', 'hqq', 'streamhqq', 'goohost', 'netuplayer'],

    # ── Active Domains (primary + common ONLY) ──
    domains=[
        # Primary
        DomainEntry('netu.to', DomainStatus.PRIMARY),
        DomainEntry('waaw.to', DomainStatus.PRIMARY,
                    note='Most commonly used alias'),
        # Common
        DomainEntry('hqq.tv', DomainStatus.COMMON),
        DomainEntry('hqq.to', DomainStatus.COMMON),
        DomainEntry('netu.ac', DomainStatus.COMMON),
        DomainEntry('waaw.ac', DomainStatus.COMMON),
        DomainEntry('goohost.com', DomainStatus.COMMON,
                    note='Delivery/player domain'),
        DomainEntry('streamhqq.com', DomainStatus.COMMON),
        DomainEntry('netuplayer.top', DomainStatus.COMMON),
    ],

    # ── Detection ──
    url_patterns=[
        r'netu\.(?:to|ac|cc|tv)',
        r'waaw\.(?:to|ac|cc|tv)',
        r'hqq\.(?:tv|to|ac)',
        r'streamhqq\.(?:com|to)',
        r'goohost\.com',
        r'netuplayer\.(?:top|com)',
    ],

    iframe_indicators=[
        'netu.to', 'netu.ac', 'waaw.to', 'waaw.ac',
        'hqq.tv', 'hqq.to', 'streamhqq', 'goohost',
        'netuplayer',
    ],
    html_indicators=[
        'netu.to', 'waaw.to', 'hqq.tv', 'goohost',
        'do_not_block',
    ],
    js_indicators=[
        'var vs =', 'eval(', 'do_not_block',
        'getVideoSources', 'jwplayer', 'hqq',
        'String.fromCharCode', 'charCodeAt',
        'escape(', 'unescape(',
    ],

    # ── Extraction Profile (Level 3 — COMPLEX) ──
    extraction=ExtractionProfile(
        method=ExtractionMethod.EVAL_JS,
        typical_format=MediaFormat.MP4,

        challenge_types=[
            ChallengeType.EVAL_CHAIN,
            ChallengeType.OBFUSCATED_JS,
            ChallengeType.CUSTOM_CRYPTO,
            ChallengeType.PACKED_JS,
            ChallengeType.ANTI_HOTLINK,
            ChallengeType.BROWSER_CHECK,
            ChallengeType.TOKEN_ROTATION,
        ],

        # ── Step-by-step ──
        flow_steps=[
            "1. Fetch embed page (format bervariasi):",
            "   - /watch?v={id}",
            "   - /player/embed_player.php?vid={id}",
            "   - /{id}",
            "   - /e/{id}",
            "",
            "2. Page berisi HEAVILY obfuscated JavaScript:",
            "   - Multiple eval() calls (3-5 layers)",
            "   - String.fromCharCode() encoding",
            "   - Custom char rotation cipher",
            "   - Variable name randomization",
            "",
            "3. Deobfuscation strategy:",
            "   a) Find outermost eval() call",
            "   b) Replace eval() with console.log() / return",
            "   c) Execute → output = next layer",
            "   d) Repeat until final JS visible",
            "   e) Final JS berisi JWPlayer setup dengan video URL",
            "",
            "4. Extract video source dari final deobfuscated JS:",
            "   - jwplayer().setup({file: 'https://...mp4'})",
            "   - ATAU: sources: [{src: 'https://...mp4'}]",
            "",
            "5. Video URL biasanya di domain goohost.com:",
            "   - https://goohost.com/{path}/video.mp4",
            "",
            "6. Download dengan headers:",
            "   - Referer: embed page URL",
            "   - Origin: embed domain",
        ],

        # ── Pseudo-code ──
        pseudo_flow=[
            "# ── Step 1: Fetch embed ──",
            "embed_url = f'https://waaw.to/watch?v={vid_id}'",
            "# ATAU: f'https://netu.to/player/embed_player.php?vid={vid_id}'",
            "# ATAU: f'https://hqq.tv/e/{vid_id}'",
            "html = fetch(embed_url)",
            "",
            "# ── Step 2: Find obfuscated JS layers ──",
            "# Layer 1: outermost eval",
            "layer1 = regex(r'eval\\((.+)\\)\\s*;?\\s*$', html, DOTALL)",
            "",
            "# ── Step 3: Iterative deobfuscation ──",
            "current = layer1",
            "for i in range(10):  # max 10 iterations safety",
            "    # Check if still has eval wrapper",
            "    if not current.strip().startswith('eval('):",
            "        break",
            "    # Strip eval( ... )",
            "    inner = current[5:-1]  # remove eval( and )",
            "    # Try to evaluate",
            "    try:",
            "        result = js_eval(inner)  # needs JS engine",
            "        current = result",
            "    except:",
            "        # Try String.fromCharCode decode",
            "        if 'String.fromCharCode' in inner:",
            "            current = decode_fromcharcode(inner)",
            "        else:",
            "            break",
            "",
            "# ── Step 4: Extract video URL from final JS ──",
            "final_js = current",
            "",
            "# Method A: JWPlayer setup",
            "video_url = regex(",
            "    r'file\\s*:\\s*[\"\\']([^\"\\']+(mp4|m3u8)[^\"\\']*)[\"\\']',",
            "    final_js",
            ")",
            "",
            "# Method B: sources array",
            "if not video_url:",
            "    video_url = regex(",
            "        r'src\\s*:\\s*[\"\\']([^\"\\']+(mp4|m3u8)[^\"\\']*)[\"\\']',",
            "        final_js",
            "    )",
            "",
            "# Method C: var assignment",
            "if not video_url:",
            "    video_url = regex(",
            "        r'var\\s+\\w+\\s*=\\s*[\"\\']([^\"\\']+(mp4|m3u8)[^\"\\']*)[\"\\']',",
            "        final_js",
            "    )",
            "",
            "# ── Step 5: Download ──",
            "video = fetch(video_url, headers={",
            "    'Referer': embed_url,",
            "    'Origin': 'https://waaw.to'",
            "})",
        ],

        # ── Implementable Hints ──
        key_regex_patterns={
            # Eval layers
            'eval_outer': r'eval\((.+)\)\s*;?\s*$',
            'eval_function': r'eval\(function\((.+?)\)\{(.+?)\}\((.+?)\)\)',

            # String.fromCharCode decoding
            'fromcharcode': r'String\.fromCharCode\(([0-9,\s]+)\)',
            'charcode_array': r'\[(\d+(?:\s*,\s*\d+)*)\]',

            # Final video URL extraction
            'jwplayer_file': r'file\s*:\s*["\']([^"\']+(?:mp4|m3u8)[^"\']*)["\']',
            'sources_src': r'src\s*:\s*["\']([^"\']+(?:mp4|m3u8)[^"\']*)["\']',
            'var_url': r'var\s+\w+\s*=\s*["\']([^"\']+(?:mp4|m3u8)[^"\']*)["\']',

            # Delivery domain
            'goohost_url': r'https?://goohost\.com/[^\s"\']+\.mp4',

            # Custom crypto patterns
            'char_rotate': r'\.charCodeAt\((\d+)\)\s*[+\-^]\s*(\d+)',
            'escape_chain': r'unescape\((.+?)\)',

            # Embed URL patterns
            'embed_watch': r'/watch\?v=([a-zA-Z0-9_-]+)',
            'embed_player': r'/player/embed_player\.php\?vid=([a-zA-Z0-9_-]+)',
            'embed_e': r'/e/([a-zA-Z0-9_-]+)',
        },

        decode_methods=[
            'eval_chain_iterative',
            'string_fromcharcode',
            'custom_char_rotation',
            'unescape_chain',
            'js_unpack_p_a_c_k_e_d',  # sometimes also packed
        ],

        requires_referer=True,
        requires_origin=True,

        header_dependencies={
            'Referer': '{embed_url}',
            'Origin': '{embed_origin}',
        },

        api_endpoints=[],  # No clean API — everything is JS obfuscated

        token_expiry_seconds=3600,  # ~1 jam

        fallback_strategy=(
            "Deobfuscation sangat kompleks dan BERUBAH sering. "
            "RECOMMENDED FALLBACK: Browser capture. "
            "1. Buka embed URL di Playwright "
            "2. Wait until JWPlayer loads "
            "3. Execute: jwplayer().getPlaylistItem().file "
            "4. Atau intercept mp4/m3u8 dari network "
            "Ini jauh lebih reliable daripada JS deobfuscation."
        ),

        failure_modes=[
            "Eval chain depth bisa berubah (3-7 layers)",
            "Custom crypto algorithm berubah per versi",
            "String.fromCharCode encoding bisa multi-format",
            "Domain rotate sering (waaw.to → netu.ac → hqq.tv)",
            "Embed URL format berubah antar domain",
            "JWPlayer version berubah → config key berbeda",
            "Token expire ~1 jam",
            "Anti-bot: browser check pada beberapa domain",
            "goohost.com delivery domain bisa blocked",
            "Referer + Origin WAJIB",
        ],

        requires_browser=True,
        browser_note=(
            "Browser capture STRONGLY RECOMMENDED. "
            "JS deobfuscation terlalu fragile — berubah tiap update. "
            "Playwright: goto embed → wait for player → "
            "intercept video URL dari network ATAU execute "
            "jwplayer().getPlaylistItem().file di console."
        ),
    ),

    tier=ServiceTier.TIER_2,
    reliability=ServiceReliability.UNSTABLE,
    last_verified='2024-12-01',
    notes=(
        'Salah satu service PALING KOMPLEKS untuk extraction. '
        'Eval chain 3-7 layers + custom crypto + frequent changes. '
        'Browser capture is the ONLY reliable method. '
        'JS deobfuscation perlu diupdate setiap kali mereka push changes. '
        'Domain rotate: netu.to ↔ waaw.to ↔ hqq.tv ↔ netu.ac.'
    ),
)

NETU_LABELS = LabelVariant(
    service_id='netu',
    labels=['Netu', 'HQQ', 'Waaw', 'NET', 'StreamHQQ', 'Goohost'],
    partial_match=True,
)

NETU_FAMILY = ServiceFamily(
    family_id='netu_family',
    display_name='Netu / Waaw / HQQ Family',
    members=[
        FamilyMember('netu', FamilyRelation.PARENT,
                     note='All domains are aliases of the same service'),
    ],
    shared_extraction_method=ExtractionMethod.EVAL_JS,
    note=(
        'netu.to, waaw.to, hqq.tv, netu.ac — '
        'semua domain sama, backend sama, protection sama. '
        'Treated as ONE service with multiple domain aliases.'
    ),
)

# ── Appendix: Netu domain variants (mirror/legacy/dead/uncertain) ──
NETU_APPENDIX_DOMAINS = [
    # Mirror
    DomainEntry('hqq.ac', DomainStatus.MIRROR),
    DomainEntry('netu.cc', DomainStatus.MIRROR),
    DomainEntry('waaw.cc', DomainStatus.MIRROR),
    DomainEntry('netu.tv', DomainStatus.MIRROR),
    DomainEntry('waaw.tv', DomainStatus.MIRROR),
    DomainEntry('hqq.watch', DomainStatus.MIRROR),
    DomainEntry('streamhqq.to', DomainStatus.MIRROR),
    DomainEntry('netuplayer.com', DomainStatus.MIRROR),
    # Legacy
    DomainEntry('hqq.com', DomainStatus.LEGACY),
    DomainEntry('netu.com', DomainStatus.LEGACY),
    DomainEntry('waaw.com', DomainStatus.LEGACY),
    DomainEntry('netu.io', DomainStatus.LEGACY),
    DomainEntry('waaw.io', DomainStatus.LEGACY),
    DomainEntry('hqq.player', DomainStatus.LEGACY),
    # Dead
    DomainEntry('hqq.nz', DomainStatus.DEAD),
    DomainEntry('netu.media', DomainStatus.DEAD),
    DomainEntry('waaw.ws', DomainStatus.DEAD),
    DomainEntry('hqq.blue', DomainStatus.DEAD),
    # Uncertain
    DomainEntry('netuplayer.xyz', DomainStatus.UNCERTAIN),
    DomainEntry('hqq.pro', DomainStatus.UNCERTAIN),
    DomainEntry('waaw.pro', DomainStatus.UNCERTAIN),
    DomainEntry('streamhqq.net', DomainStatus.UNCERTAIN),
]


# ══════════════════════════════════════════════════════════
#  COLLECTIONS
# ══════════════════════════════════════════════════════════

ALL_SERVICES_BATCH2 = [
    TURBOVID,
    VIDEY,
    VK_VIDEO,
    GOOGLE_VIDEO,
    SIBNET,
    NETU,
]

ALL_FAMILIES_BATCH2 = [
    GOOGLE_FAMILY,
    NETU_FAMILY,
]

ALL_LABELS_BATCH2 = [
    TURBOVID_LABELS,
    VIDEY_LABELS,
    VK_VIDEO_LABELS,
    GOOGLE_VIDEO_LABELS,
    SIBNET_LABELS,
    NETU_LABELS,
]

# All appendix domains grouped
ALL_APPENDIX_DOMAINS = {
    'turbovid': TURBOVID_APPENDIX_DOMAINS,
    'videy': VIDEY_APPENDIX_DOMAINS,
    'vk_video': VK_VIDEO_APPENDIX_DOMAINS,
    'google_video': GOOGLE_VIDEO_APPENDIX_DOMAINS,
    'sibnet': SIBNET_APPENDIX_DOMAINS,
    'netu': NETU_APPENDIX_DOMAINS,
}


# ══════════════════════════════════════════════════════════
#  REGISTRATION
# ══════════════════════════════════════════════════════════

def register_new_services_batch2(
    registry: EmbedServiceRegistry = None
):
    """
    Register 6 new services + families + labels + appendix domains.
    """
    if registry is None:
        registry = EmbedServiceRegistry.get_instance()

    # Register families
    for family in ALL_FAMILIES_BATCH2:
        registry.register_family(family)

    # Register services
    registry.register_services(ALL_SERVICES_BATCH2)

    # Register label variants
    for label_variant in ALL_LABELS_BATCH2:
        registry.register_label_variants(label_variant)

    # Register appendix domains onto their respective services
    for service_id, appendix_domains in ALL_APPENDIX_DOMAINS.items():
        service = registry.get_service(service_id)
        if service and appendix_domains:
            # Extend domains list with appendix
            service.domains.extend(appendix_domains)

    return registry