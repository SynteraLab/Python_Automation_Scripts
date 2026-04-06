# player_intelligence/profiles.py
"""
Declarative player profile database.

Each supported player/framework family is described by a single
PlayerProfile instance containing all detection markers, version
patterns, config extraction patterns, output-type hints, and
wrapper metadata.

EXTENSIBILITY:
    Adding a new player family requires only:
    1. Add a PlayerFamily enum member in models.py
    2. Add a PlayerProfile instance in this file
    3. Optionally add a config extractor in config_extractors.py
    Zero changes to the detection engine or scoring logic.
"""

from __future__ import annotations

import re
from typing import Sequence

from .models import (
    ConfigType,
    EvidenceCategory,
    MarkerRule,
    OutputType,
    PlayerConfigPattern,
    PlayerFamily,
    PlayerProfile,
    PlayerVersionPattern,
    WrapperHints,
)


# ═══════════════════════════════════════════════════════════════
# HELPER — compile pattern with IGNORECASE + DOTALL defaults
# ═══════════════════════════════════════════════════════════════

_IC = re.IGNORECASE
_ICS = re.IGNORECASE | re.DOTALL


def _m(
    pattern: str,
    category: EvidenceCategory,
    weight: float,
    description: str,
    *,
    negates: bool = False,
    flags: int = _IC,
) -> MarkerRule:
    """Shorthand factory for MarkerRule with compiled regex."""
    return MarkerRule(
        pattern=re.compile(pattern, flags),
        category=category,
        weight=weight,
        description=description,
        negates=negates,
    )


def _vp(
    pattern: str,
    source: str,
    description: str = "",
    *,
    version_group: int | str = 1,
    flags: int = _IC,
) -> PlayerVersionPattern:
    """Shorthand factory for PlayerVersionPattern."""
    return PlayerVersionPattern(
        pattern=re.compile(pattern, flags),
        version_group=version_group,
        source=source,
        description=description,
    )


def _cp(
    pattern: str,
    extractor_fn: str,
    config_type: ConfigType,
    description: str = "",
    *,
    priority: int = 50,
    flags: int = _ICS,
) -> PlayerConfigPattern:
    """Shorthand factory for PlayerConfigPattern."""
    return PlayerConfigPattern(
        pattern=re.compile(pattern, flags),
        extractor_fn=extractor_fn,
        config_type=config_type,
        description=description,
        priority=priority,
    )


# ═══════════════════════════════════════════════════════════════
# EVIDENCE CATEGORY SHORTHANDS
# ═══════════════════════════════════════════════════════════════

_SS = EvidenceCategory.SCRIPT_SRC
_DC = EvidenceCategory.DOM_CSS
_IJ = EvidenceCategory.INLINE_JS
_GV = EvidenceCategory.GLOBAL_VAR
_DA = EvidenceCategory.DATA_ATTR


# ═══════════════════════════════════════════════════════════════
# PROFILE: JWPLAYER
# ═══════════════════════════════════════════════════════════════

_JWPLAYER_PROFILE = PlayerProfile(
    family=PlayerFamily.JWPLAYER,
    canonical_name="JW Player",
    aliases=("jwplayer", "jw", "jw player", "jw8", "jw7", "jwplayer8"),
    homepage="https://www.jwplayer.com/",
    script_src_markers=(
        _m(r'["\']([^"\']*jwplayer[^"\']*\.js[^"\']*)["\']',
           _SS, 0.40, "jwplayer JS file in script src"),
        _m(r'cdn\.jwplayer\.com',
           _SS, 0.40, "JWPlayer CDN domain"),
        _m(r'content\.jwplatform\.com',
           _SS, 0.35, "JW Platform content CDN"),
        _m(r'ssl\.p\.jwpcdn\.com',
           _SS, 0.35, "JWPlayer static CDN"),
    ),
    dom_css_markers=(
        _m(r'class\s*=\s*["\'][^"\']*\bjw-video\b',
           _DC, 0.20, "jw-video CSS class"),
        _m(r'class\s*=\s*["\'][^"\']*\bjw-wrapper\b',
           _DC, 0.20, "jw-wrapper CSS class"),
        _m(r'class\s*=\s*["\'][^"\']*\bjwplayer\b',
           _DC, 0.20, "jwplayer CSS class"),
        _m(r'class\s*=\s*["\'][^"\']*\bjw-controls\b',
           _DC, 0.15, "jw-controls CSS class"),
        _m(r'id\s*=\s*["\']jwplayer',
           _DC, 0.15, "jwplayer element ID"),
    ),
    inline_js_markers=(
        _m(r'\bjwplayer\s*\(\s*["\']',
           _IJ, 0.35, "jwplayer() constructor call"),
        _m(r'\.setup\s*\(\s*\{',
           _IJ, 0.25, ".setup({}) call (JW-style)"),
        _m(r'\bjwplayer\.key\s*=',
           _IJ, 0.30, "jwplayer.key assignment"),
        _m(r'\bjwplayer\s*\(\s*\)\s*\.on\s*\(',
           _IJ, 0.20, "jwplayer().on() event binding"),
        _m(r'\bjwDefaults\b',
           _IJ, 0.15, "jwDefaults object reference"),
    ),
    global_variable_markers=(
        _m(r'\bwindow\s*\.\s*jwplayer\b',
           _GV, 0.25, "window.jwplayer global"),
        _m(r'\btypeof\s+jwplayer\b',
           _GV, 0.20, "typeof jwplayer check"),
    ),
    data_attribute_markers=(
        _m(r'data-jw-',
           _DA, 0.15, "data-jw-* attribute"),
        _m(r'data-player-id\s*=',
           _DA, 0.10, "data-player-id attribute"),
    ),
    version_patterns=(
        _vp(r'jwplayer[.\-/]v?(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from script path"),
        _vp(r'jwplayer\.version\s*[=:]\s*["\'](\d+\.\d+(?:\.\d+)?)',
            "inline_js", "jwplayer.version assignment"),
        _vp(r'JW\s*Player\s+v?(\d+\.\d+(?:\.\d+)?)',
            "comment", "JW Player version in comment"),
        _vp(r'jwplayer-(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from filename suffix"),
    ),
    config_patterns=(
        _cp(r'jwplayer\s*\([^)]*\)\s*\.\s*setup\s*\(\s*(\{)',
            "setup_call", ConfigType.SETUP_CALL,
            "jwplayer().setup({...})", priority=10),
        _cp(r'jwplayer\s*\(\s*\)\s*\.\s*load\s*\(\s*(\[)',
            "setup_call", ConfigType.SOURCES_ARRAY,
            "jwplayer().load([...])", priority=30),
        _cp(r'(?:playerConfig|jwConfig|jwSetup)\s*=\s*(\{)',
            "window_var", ConfigType.WINDOW_VAR,
            "JW config variable assignment", priority=20),
        _cp(r'sources\s*:\s*(\[)',
            "sources_array", ConfigType.SOURCES_ARRAY,
            "JW sources array", priority=40),
    ),
    probable_output_types=(
        OutputType.MP4, OutputType.HLS, OutputType.DASH,
    ),
    wrapper_hints=WrapperHints(
        is_commonly_wrapped=True,
        iframe_src_hints=(
            re.compile(r'/embed/|/player/', _IC),
        ),
    ),
    confidence_ceiling=0.75,
)


# ═══════════════════════════════════════════════════════════════
# PROFILE: VIDEO.JS
# ═══════════════════════════════════════════════════════════════

_VIDEOJS_PROFILE = PlayerProfile(
    family=PlayerFamily.VIDEOJS,
    canonical_name="Video.js",
    aliases=("videojs", "video.js", "vjs", "video-js"),
    homepage="https://videojs.com/",
    script_src_markers=(
        _m(r'["\']([^"\']*video(?:\.min)?\.js[^"\']*)["\']',
           _SS, 0.35, "video.js file in script src"),
        _m(r'vjs\.zencdn\.net',
           _SS, 0.40, "Video.js ZenCDN"),
        _m(r'cdn\.jsdelivr\.net/npm/video\.js',
           _SS, 0.38, "Video.js on jsDelivr CDN"),
        _m(r'unpkg\.com/video\.js',
           _SS, 0.38, "Video.js on unpkg CDN"),
        _m(r'videojs-contrib-hls',
           _SS, 0.25, "videojs HLS contrib plugin"),
        _m(r'videojs-http-streaming',
           _SS, 0.25, "videojs HTTP streaming plugin"),
    ),
    dom_css_markers=(
        _m(r'class\s*=\s*["\'][^"\']*\bvideo-js\b',
           _DC, 0.25, "video-js CSS class"),
        _m(r'class\s*=\s*["\'][^"\']*\bvjs-',
           _DC, 0.20, "vjs-* CSS class prefix"),
        _m(r'class\s*=\s*["\'][^"\']*\bvjs-default-skin\b',
           _DC, 0.20, "vjs-default-skin CSS class"),
        _m(r'<video[^>]+class\s*=\s*["\'][^"\']*video-js',
           _DC, 0.25, "<video> with video-js class"),
    ),
    inline_js_markers=(
        _m(r'\bvideojs\s*\(\s*["\']',
           _IJ, 0.30, "videojs() init call"),
        _m(r'\bvideojs\.registerPlugin\b',
           _IJ, 0.25, "videojs.registerPlugin call"),
        _m(r'\bvideojs\.options\b',
           _IJ, 0.20, "videojs.options reference"),
        _m(r'\bvideojs\.getPlayer\b',
           _IJ, 0.20, "videojs.getPlayer call"),
    ),
    global_variable_markers=(
        _m(r'\bwindow\s*\.\s*videojs\b',
           _GV, 0.25, "window.videojs global"),
        _m(r'\btypeof\s+videojs\b',
           _GV, 0.20, "typeof videojs check"),
    ),
    data_attribute_markers=(
        _m(r'data-setup\s*=\s*["\']?\{',
           _DA, 0.20, "data-setup attribute with JSON config"),
        _m(r'data-vjs-player',
           _DA, 0.15, "data-vjs-player attribute"),
    ),
    version_patterns=(
        _vp(r'video\.js\s+v?(\d+\.\d+(?:\.\d+)?)',
            "comment", "Video.js version in comment"),
        _vp(r'videojs\.VERSION\s*[=:]\s*["\'](\d+\.\d+(?:\.\d+)?)',
            "inline_js", "videojs.VERSION assignment"),
        _vp(r'vjs\.zencdn\.net/(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from ZenCDN path"),
        _vp(r'video\.js@(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from npm-style path"),
    ),
    config_patterns=(
        _cp(r'videojs\s*\(\s*["\'][^"\']+["\']\s*,\s*(\{)',
            "setup_call", ConfigType.SETUP_CALL,
            "videojs(id, {...})", priority=10),
        _cp(r'data-setup\s*=\s*["\'](\{[^"\']*\})["\']',
            "data_attr_json", ConfigType.DATA_ATTR,
            "data-setup JSON attribute", priority=15),
        _cp(r'(?:vjsConfig|videojsConfig|playerOptions)\s*=\s*(\{)',
            "window_var", ConfigType.WINDOW_VAR,
            "videojs config variable assignment", priority=20),
    ),
    probable_output_types=(
        OutputType.MP4, OutputType.HLS, OutputType.DASH,
    ),
    wrapper_hints=WrapperHints(
        is_commonly_wrapped=True,
    ),
    confidence_ceiling=0.75,
)


# ═══════════════════════════════════════════════════════════════
# PROFILE: CLAPPR
# ═══════════════════════════════════════════════════════════════

_CLAPPR_PROFILE = PlayerProfile(
    family=PlayerFamily.CLAPPR,
    canonical_name="Clappr",
    aliases=("clappr", "clappr.io", "clappr-player"),
    homepage="https://clappr.io/",
    script_src_markers=(
        _m(r'["\']([^"\']*clappr(?:\.min)?\.js[^"\']*)["\']',
           _SS, 0.40, "clappr JS file in script src"),
        _m(r'cdn\.clappr\.io',
           _SS, 0.40, "Clappr CDN domain"),
        _m(r'cdn\.jsdelivr\.net/npm/clappr',
           _SS, 0.38, "Clappr on jsDelivr CDN"),
        _m(r'unpkg\.com/clappr',
           _SS, 0.38, "Clappr on unpkg CDN"),
        _m(r'clappr-level-selector',
           _SS, 0.20, "Clappr level selector plugin"),
        _m(r'clappr-chromecast-plugin',
           _SS, 0.20, "Clappr Chromecast plugin"),
    ),
    dom_css_markers=(
        _m(r'class\s*=\s*["\'][^"\']*\bclappr-',
           _DC, 0.20, "clappr-* CSS class prefix"),
        _m(r'class\s*=\s*["\'][^"\']*\bplayer-poster\b',
           _DC, 0.10, "player-poster CSS class (Clappr)"),
        _m(r'\[data-clappr\]',
           _DC, 0.20, "data-clappr attribute selector"),
        _m(r'class\s*=\s*["\'][^"\']*\bcontainer-layer\b',
           _DC, 0.08, "container-layer class (Clappr generic)"),
    ),
    inline_js_markers=(
        _m(r'\bnew\s+Clappr\s*\.\s*Player\s*\(',
           _IJ, 0.35, "new Clappr.Player() constructor"),
        _m(r'\bClappr\s*\.\s*Player\b',
           _IJ, 0.30, "Clappr.Player reference"),
        _m(r'\bClappr\s*\.\s*Loader\b',
           _IJ, 0.20, "Clappr.Loader reference"),
        _m(r'\bClappr\s*\.\s*Mediator\b',
           _IJ, 0.15, "Clappr.Mediator reference"),
        _m(r'\bClappr\s*\.\s*Events\b',
           _IJ, 0.15, "Clappr.Events reference"),
    ),
    global_variable_markers=(
        _m(r'\bwindow\s*\.\s*Clappr\b',
           _GV, 0.25, "window.Clappr global"),
        _m(r'\btypeof\s+Clappr\b',
           _GV, 0.20, "typeof Clappr check"),
    ),
    data_attribute_markers=(
        _m(r'data-clappr-',
           _DA, 0.15, "data-clappr-* attribute"),
        _m(r'data-clappr\s*=',
           _DA, 0.15, "data-clappr attribute"),
    ),
    version_patterns=(
        _vp(r'[Cc]lappr\s+v?(\d+\.\d+(?:\.\d+)?)',
            "comment", "Clappr version in comment"),
        _vp(r'clappr@(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from npm-style path"),
        _vp(r'clappr[/\-]v?(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from script path"),
    ),
    config_patterns=(
        _cp(r'new\s+Clappr\s*\.\s*Player\s*\(\s*(\{)',
            "setup_call", ConfigType.INIT_BLOCK,
            "new Clappr.Player({...})", priority=10),
        _cp(r'(?:clapprConfig|playerConfig)\s*=\s*(\{)',
            "window_var", ConfigType.WINDOW_VAR,
            "Clappr config variable", priority=20),
    ),
    probable_output_types=(
        OutputType.MP4, OutputType.HLS,
    ),
    wrapper_hints=WrapperHints(
        is_commonly_wrapped=True,
    ),
    confidence_ceiling=0.72,
)


# ═══════════════════════════════════════════════════════════════
# PROFILE: PLYR
# ═══════════════════════════════════════════════════════════════

_PLYR_PROFILE = PlayerProfile(
    family=PlayerFamily.PLYR,
    canonical_name="Plyr",
    aliases=("plyr", "plyr.io", "plyr-player"),
    homepage="https://plyr.io/",
    script_src_markers=(
        _m(r'["\']([^"\']*plyr(?:\.polyfilled)?(?:\.min)?\.js[^"\']*)["\']',
           _SS, 0.40, "plyr JS file in script src"),
        _m(r'cdn\.plyr\.io',
           _SS, 0.40, "Plyr CDN domain"),
        _m(r'cdn\.jsdelivr\.net/npm/plyr',
           _SS, 0.38, "Plyr on jsDelivr CDN"),
    ),
    dom_css_markers=(
        _m(r'class\s*=\s*["\'][^"\']*\bplyr\b',
           _DC, 0.20, "plyr CSS class"),
        _m(r'class\s*=\s*["\'][^"\']*\bplyr__',
           _DC, 0.20, "plyr__* CSS class prefix"),
        _m(r'class\s*=\s*["\'][^"\']*\bplyr--',
           _DC, 0.15, "plyr--* modifier CSS class"),
    ),
    inline_js_markers=(
        _m(r'\bnew\s+Plyr\s*\(',
           _IJ, 0.35, "new Plyr() constructor"),
        _m(r'\bPlyr\s*\.\s*setup\s*\(',
           _IJ, 0.30, "Plyr.setup() call"),
        _m(r'\bPlyr\.supported\b',
           _IJ, 0.15, "Plyr.supported() check"),
    ),
    global_variable_markers=(
        _m(r'\bwindow\s*\.\s*Plyr\b',
           _GV, 0.25, "window.Plyr global"),
        _m(r'\btypeof\s+Plyr\b',
           _GV, 0.20, "typeof Plyr check"),
    ),
    data_attribute_markers=(
        _m(r'data-plyr-',
           _DA, 0.20, "data-plyr-* attribute"),
        _m(r'data-plyr-provider\s*=',
           _DA, 0.20, "data-plyr-provider attribute"),
        _m(r'data-plyr-embed-id\s*=',
           _DA, 0.18, "data-plyr-embed-id attribute"),
    ),
    version_patterns=(
        _vp(r'[Pp]lyr\s+v?(\d+\.\d+(?:\.\d+)?)',
            "comment", "Plyr version in comment"),
        _vp(r'plyr@(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from npm-style path"),
        _vp(r'cdn\.plyr\.io/(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from Plyr CDN path"),
    ),
    config_patterns=(
        _cp(r'new\s+Plyr\s*\(\s*[^,]+,\s*(\{)',
            "setup_call", ConfigType.INIT_BLOCK,
            "new Plyr(el, {...})", priority=10),
        _cp(r'Plyr\.setup\s*\(\s*[^,]+,\s*(\{)',
            "setup_call", ConfigType.SETUP_CALL,
            "Plyr.setup(selector, {...})", priority=15),
        _cp(r'data-plyr-config\s*=\s*["\'](\{[^"\']*\})["\']',
            "data_attr_json", ConfigType.DATA_ATTR,
            "data-plyr-config JSON attribute", priority=20),
    ),
    probable_output_types=(
        OutputType.MP4, OutputType.HLS,
    ),
    confidence_ceiling=0.72,
)


# ═══════════════════════════════════════════════════════════════
# PROFILE: DPLAYER
# ═══════════════════════════════════════════════════════════════

_DPLAYER_PROFILE = PlayerProfile(
    family=PlayerFamily.DPLAYER,
    canonical_name="DPlayer",
    aliases=("dplayer", "d-player"),
    homepage="https://dplayer.diygod.dev/",
    script_src_markers=(
        _m(r'["\']([^"\']*[Dd][Pp]layer(?:\.min)?\.js[^"\']*)["\']',
           _SS, 0.40, "DPlayer JS file in script src"),
        _m(r'cdn\.jsdelivr\.net/npm/dplayer',
           _SS, 0.38, "DPlayer on jsDelivr CDN"),
        _m(r'unpkg\.com/dplayer',
           _SS, 0.38, "DPlayer on unpkg CDN"),
    ),
    dom_css_markers=(
        _m(r'class\s*=\s*["\'][^"\']*\bdplayer\b',
           _DC, 0.20, "dplayer CSS class"),
        _m(r'class\s*=\s*["\'][^"\']*\bdplayer-',
           _DC, 0.20, "dplayer-* CSS class prefix"),
        _m(r'id\s*=\s*["\']dplayer',
           _DC, 0.15, "dplayer element ID"),
    ),
    inline_js_markers=(
        _m(r'\bnew\s+DPlayer\s*\(',
           _IJ, 0.35, "new DPlayer() constructor"),
        _m(r'\bdp\s*=\s*new\s+DPlayer\b',
           _IJ, 0.30, "dp = new DPlayer assignment"),
        _m(r'\bDPlayer\s*\.\s*version\b',
           _IJ, 0.15, "DPlayer.version reference"),
    ),
    global_variable_markers=(
        _m(r'\bwindow\s*\.\s*DPlayer\b',
           _GV, 0.25, "window.DPlayer global"),
        _m(r'\btypeof\s+DPlayer\b',
           _GV, 0.20, "typeof DPlayer check"),
    ),
    data_attribute_markers=(
        _m(r'data-dplayer-',
           _DA, 0.15, "data-dplayer-* attribute"),
    ),
    version_patterns=(
        _vp(r'[Dd][Pp]layer\s+v?(\d+\.\d+(?:\.\d+)?)',
            "comment", "DPlayer version in comment"),
        _vp(r'dplayer@(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from npm-style path"),
    ),
    config_patterns=(
        _cp(r'new\s+DPlayer\s*\(\s*(\{)',
            "setup_call", ConfigType.INIT_BLOCK,
            "new DPlayer({...})", priority=10),
        _cp(r'(?:dplayerConfig|dpConfig)\s*=\s*(\{)',
            "window_var", ConfigType.WINDOW_VAR,
            "DPlayer config variable", priority=20),
    ),
    probable_output_types=(
        OutputType.MP4, OutputType.HLS, OutputType.DASH, OutputType.FLV,
    ),
    confidence_ceiling=0.72,
)


# ═══════════════════════════════════════════════════════════════
# PROFILE: ARTPLAYER
# ═══════════════════════════════════════════════════════════════

_ARTPLAYER_PROFILE = PlayerProfile(
    family=PlayerFamily.ARTPLAYER,
    canonical_name="ArtPlayer",
    aliases=("artplayer", "art-player", "art_player"),
    homepage="https://artplayer.org/",
    script_src_markers=(
        _m(r'["\']([^"\']*artplayer(?:\.min)?\.js[^"\']*)["\']',
           _SS, 0.40, "artplayer JS file in script src"),
        _m(r'cdn\.jsdelivr\.net/npm/artplayer',
           _SS, 0.38, "ArtPlayer on jsDelivr CDN"),
        _m(r'unpkg\.com/artplayer',
           _SS, 0.38, "ArtPlayer on unpkg CDN"),
        _m(r'artplayer-plugin-',
           _SS, 0.20, "ArtPlayer plugin script"),
    ),
    dom_css_markers=(
        _m(r'class\s*=\s*["\'][^"\']*\bartplayer-app\b',
           _DC, 0.25, "artplayer-app CSS class"),
        _m(r'class\s*=\s*["\'][^"\']*\bart-',
           _DC, 0.18, "art-* CSS class prefix"),
        _m(r'id\s*=\s*["\']artplayer',
           _DC, 0.15, "artplayer element ID"),
    ),
    inline_js_markers=(
        _m(r'\bnew\s+Artplayer\s*\(',
           _IJ, 0.35, "new Artplayer() constructor"),
        _m(r'\bnew\s+ArtPlayer\s*\(',
           _IJ, 0.35, "new ArtPlayer() constructor (alt case)"),
        _m(r'\bArtplayer\s*\.\s*version\b',
           _IJ, 0.15, "Artplayer.version reference"),
        _m(r'\bArtplayer\s*\.\s*DEFAULTS\b',
           _IJ, 0.15, "Artplayer.DEFAULTS reference"),
    ),
    global_variable_markers=(
        _m(r'\bwindow\s*\.\s*Artplayer\b',
           _GV, 0.25, "window.Artplayer global"),
        _m(r'\btypeof\s+Artplayer\b',
           _GV, 0.20, "typeof Artplayer check"),
    ),
    data_attribute_markers=(),  # ArtPlayer rarely uses data-* attributes
    version_patterns=(
        _vp(r'[Aa]rt[Pp]layer\s+v?(\d+\.\d+(?:\.\d+)?)',
            "comment", "ArtPlayer version in comment"),
        _vp(r'artplayer@(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from npm-style path"),
        _vp(r'artplayer/(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from CDN path"),
    ),
    config_patterns=(
        _cp(r'new\s+Art[Pp]layer\s*\(\s*(\{)',
            "setup_call", ConfigType.INIT_BLOCK,
            "new Artplayer({...})", priority=10),
        _cp(r'(?:artConfig|artplayerConfig)\s*=\s*(\{)',
            "window_var", ConfigType.WINDOW_VAR,
            "ArtPlayer config variable", priority=20),
    ),
    probable_output_types=(
        OutputType.MP4, OutputType.HLS, OutputType.DASH, OutputType.FLV,
    ),
    confidence_ceiling=0.72,
)


# ═══════════════════════════════════════════════════════════════
# PROFILE: GENERIC HLS (hls.js without named player)
# ═══════════════════════════════════════════════════════════════

_GENERIC_HLS_PROFILE = PlayerProfile(
    family=PlayerFamily.GENERIC_HLS,
    canonical_name="Generic HLS (hls.js)",
    aliases=("hls.js", "hlsjs", "hls", "native_hls"),
    homepage="https://github.com/video-dev/hls.js/",
    script_src_markers=(
        _m(r'["\']([^"\']*hls(?:\.light)?(?:\.min)?\.js[^"\']*)["\']',
           _SS, 0.35, "hls.js file in script src"),
        _m(r'cdn\.jsdelivr\.net/npm/hls\.js',
           _SS, 0.35, "hls.js on jsDelivr CDN"),
        _m(r'cdnjs\.cloudflare\.com/ajax/libs/hls\.js',
           _SS, 0.35, "hls.js on cdnjs"),
    ),
    dom_css_markers=(
        # Generic HLS typically has no distinctive CSS; bare <video> tag
        _m(r'<video\s[^>]*(?:id|class)\s*=',
           _DC, 0.05, "Bare <video> element with id/class"),
    ),
    inline_js_markers=(
        _m(r'\bnew\s+Hls\s*\(',
           _IJ, 0.30, "new Hls() constructor"),
        _m(r'\bHls\s*\.\s*isSupported\s*\(',
           _IJ, 0.25, "Hls.isSupported() call"),
        _m(r'\.loadSource\s*\(\s*["\']',
           _IJ, 0.25, "hls.loadSource() call"),
        _m(r'\.attachMedia\s*\(',
           _IJ, 0.20, "hls.attachMedia() call"),
        _m(r'Hls\s*\.\s*Events\s*\.',
           _IJ, 0.15, "Hls.Events reference"),
    ),
    global_variable_markers=(
        _m(r'\bwindow\s*\.\s*Hls\b',
           _GV, 0.25, "window.Hls global"),
        _m(r'\btypeof\s+Hls\b',
           _GV, 0.20, "typeof Hls check"),
    ),
    data_attribute_markers=(),
    version_patterns=(
        _vp(r'hls\.js\s+v?(\d+\.\d+(?:\.\d+)?)',
            "comment", "hls.js version in comment"),
        _vp(r'hls\.js@(\d+\.\d+(?:\.\d+)?)',
            "script_src", "Version from npm-style path"),
        _vp(r'Hls\.version\s*[=:]\s*["\'](\d+\.\d+(?:\.\d+)?)',
            "inline_js", "Hls.version assignment"),
    ),
    config_patterns=(
        _cp(r'new\s+Hls\s*\(\s*(\{)',
            "setup_call", ConfigType.INIT_BLOCK,
            "new Hls({...})", priority=10),
        _cp(r'\.loadSource\s*\(\s*["\']([^"\']+)["\']',
            "setup_call", ConfigType.SETUP_CALL,
            "hls.loadSource(url)", priority=15),
        _cp(r'(?:hlsConfig|hlsOptions)\s*=\s*(\{)',
            "window_var", ConfigType.WINDOW_VAR,
            "HLS config variable", priority=20),
    ),
    probable_output_types=(
        OutputType.HLS,
    ),
    wrapper_hints=WrapperHints(
        commonly_wraps_others=False,
        is_commonly_wrapped=True,
    ),
    confidence_ceiling=0.70,
)


# ═══════════════════════════════════════════════════════════════
# PROFILE: CUSTOM IFRAME WRAPPER
# ═══════════════════════════════════════════════════════════════

_CUSTOM_IFRAME_PROFILE = PlayerProfile(
    family=PlayerFamily.CUSTOM_IFRAME,
    canonical_name="Custom Iframe Wrapper",
    aliases=("iframe", "iframe_wrapper", "embed_wrapper", "custom_wrapper"),
    script_src_markers=(),  # Iframe wrappers have no characteristic scripts
    dom_css_markers=(
        _m(r'<iframe\s[^>]*src\s*=\s*["\'][^"\']*(?:embed|player)[^"\']*["\']',
           _DC, 0.30, "iframe with embed/player in src"),
        _m(r'<iframe\s[^>]*allowfullscreen',
           _DC, 0.15, "iframe with allowfullscreen"),
        _m(r'<iframe\s[^>]*src\s*=\s*["\'][^"\']*\.(?:m3u8|mp4)',
           _DC, 0.25, "iframe src pointing to media file"),
    ),
    inline_js_markers=(
        _m(r'document\s*\.\s*createElement\s*\(\s*["\']iframe["\']\s*\)',
           _IJ, 0.20, "Dynamic iframe creation"),
        _m(r'iframe\s*\.\s*src\s*=',
           _IJ, 0.15, "iframe.src assignment"),
        _m(r'\.contentWindow\s*\.\s*postMessage\s*\(',
           _IJ, 0.10, "postMessage to iframe (wrapper comm)"),
    ),
    global_variable_markers=(),
    data_attribute_markers=(
        _m(r'data-embed-',
           _DA, 0.10, "data-embed-* attribute"),
        _m(r'data-player-src\s*=',
           _DA, 0.10, "data-player-src attribute"),
    ),
    version_patterns=(),  # No version for generic iframe wrappers
    config_patterns=(
        _cp(r'<iframe\s[^>]*src\s*=\s*["\']([^"\']+)["\']',
            "embed_url", ConfigType.EMBED_URL,
            "iframe src URL extraction", priority=10),
    ),
    probable_output_types=(
        OutputType.IFRAME,
    ),
    wrapper_hints=WrapperHints(
        commonly_wraps_others=True,
        is_commonly_wrapped=False,
        iframe_src_hints=(
            re.compile(r'/embed/', _IC),
            re.compile(r'/player/', _IC),
            re.compile(r'/e/', _IC),
            re.compile(r'\.m3u8', _IC),
        ),
    ),
    confidence_ceiling=0.65,
)


# ═══════════════════════════════════════════════════════════════
# PROFILE REGISTRY
# ═══════════════════════════════════════════════════════════════

# ADAPTATION POINT: The host repository may use Phase 1's extractor
# registry pattern to register player profiles.  If so, replace
# this dict-based registry with registry.register_player_profile()
# calls during bootstrap.

PROFILES: dict[PlayerFamily, PlayerProfile] = {
    PlayerFamily.JWPLAYER: _JWPLAYER_PROFILE,
    PlayerFamily.VIDEOJS: _VIDEOJS_PROFILE,
    PlayerFamily.CLAPPR: _CLAPPR_PROFILE,
    PlayerFamily.PLYR: _PLYR_PROFILE,
    PlayerFamily.DPLAYER: _DPLAYER_PROFILE,
    PlayerFamily.ARTPLAYER: _ARTPLAYER_PROFILE,
    PlayerFamily.GENERIC_HLS: _GENERIC_HLS_PROFILE,
    PlayerFamily.CUSTOM_IFRAME: _CUSTOM_IFRAME_PROFILE,
}


# ═══════════════════════════════════════════════════════════════
# PUBLIC ACCESSORS
# ═══════════════════════════════════════════════════════════════

def get_profile(family: PlayerFamily) -> PlayerProfile | None:
    """Retrieve a player profile by family enum."""
    return PROFILES.get(family)


def get_all_profiles() -> list[PlayerProfile]:
    """Return all registered profiles as a list."""
    return list(PROFILES.values())


def get_families() -> list[PlayerFamily]:
    """Return all registered player family enums."""
    return list(PROFILES.keys())


def register_profile(profile: PlayerProfile) -> None:
    """
    Register or replace a player profile at runtime.

    This allows downstream code or plugins to inject
    additional player profiles without editing this file.
    """
    # ADAPTATION POINT: May integrate with Phase 1 registry
    # for unified registration events and logging.
    PROFILES[profile.family] = profile


def get_profiles_for_detection() -> list[PlayerProfile]:
    """
    Return profiles ordered for detection scanning.

    Named player families are scanned first (alphabetical),
    followed by GENERIC_HLS and CUSTOM_IFRAME last.
    This ordering ensures that specific players are matched
    before generic/wrapper categories.
    """
    specific: list[PlayerProfile] = []
    generic: list[PlayerProfile] = []
    for fam in sorted(PROFILES.keys(), key=lambda f: f.value):
        profile = PROFILES[fam]
        if fam in (PlayerFamily.GENERIC_HLS, PlayerFamily.CUSTOM_IFRAME):
            generic.append(profile)
        else:
            specific.append(profile)
    return specific + generic