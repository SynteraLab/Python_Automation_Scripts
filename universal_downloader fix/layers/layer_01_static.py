"""
layer_01_static.py — Static HTML Deep Analysis

Analisis source HTML TANPA rendering JavaScript:
- Semua tag media (img, video, audio, source, picture, embed, object)
- Semua link ke file media (<a href>)
- Meta tags (og:image, og:video, twitter:image, etc)
- Structured data (JSON-LD, microdata)
- CSS inline & <style> backgrounds
- Lazy-load attributes
- Srcset parsing
- Inline scripts (tanpa AST, regex saja)
- Comments HTML (sering ada URL tersembunyi)
"""

import re
import json
import logging
from typing import Dict, List, Set, Optional
from bs4 import BeautifulSoup, Comment
from urllib.parse import urljoin

from layers.base import BaseLayer, LayerResult
from core.browser import BrowserCaptureResult
from core.session import SessionManager
from utils.media_types import MediaTypes
from utils.url_utils import URLUtils
from utils.pattern_matcher import PatternMatcher

logger = logging.getLogger(__name__)


class StaticAnalysisLayer(BaseLayer):
    
    LAYER_NAME = "layer_01_static"
    LAYER_DESCRIPTION = "Static HTML Deep Analysis"
    
    async def execute(self, url, recon, capture, session):
        """Analisis HTML mentah (sebelum JS rendering)"""
        
        # Ambil HTML dari session (raw, non-rendered)
        resp = session.get(url)
        if not resp or not resp.body:
            self.add_error("Failed to fetch HTML")
            return
        
        raw_html = resp.body
        base_url = url
        
        # Parse dengan BeautifulSoup
        soup = BeautifulSoup(raw_html, 'lxml')
        
        # Juga analisis rendered HTML dari browser (jika ada)
        rendered_html = capture.page_html if capture else ""
        rendered_soup = None
        if rendered_html:
            rendered_soup = BeautifulSoup(rendered_html, 'lxml')
        
        # Store raw data untuk layers lain
        self._result.raw_data['raw_html'] = raw_html
        self._result.raw_data['rendered_html'] = rendered_html
        
        seen_urls: Set[str] = set()
        
        # ── 1. IMG Tags ──
        self._parse_img_tags(soup, base_url, seen_urls)
        if rendered_soup:
            self._parse_img_tags(rendered_soup, base_url, seen_urls, source_suffix=" (rendered)")
        
        # ── 2. Picture + Source (responsive images) ──
        self._parse_picture_tags(soup, base_url, seen_urls)
        
        # ── 3. Video Tags ──
        self._parse_video_tags(soup, base_url, seen_urls)
        if rendered_soup:
            self._parse_video_tags(rendered_soup, base_url, seen_urls, source_suffix=" (rendered)")
        
        # ── 4. Audio Tags ──
        self._parse_audio_tags(soup, base_url, seen_urls)
        
        # ── 5. Iframe Tags ──
        self._parse_iframe_tags(soup, base_url, seen_urls)
        if rendered_soup:
            self._parse_iframe_tags(rendered_soup, base_url, seen_urls, source_suffix=" (rendered)")
        
        # ── 6. Embed & Object Tags ──
        self._parse_embed_object(soup, base_url, seen_urls)
        
        # ── 7. Links to Media Files ──
        self._parse_media_links(soup, base_url, seen_urls)
        
        # ── 8. Meta Tags (OG, Twitter, etc) ──
        self._parse_meta_tags(soup, base_url, seen_urls)
        
        # ── 9. Structured Data (JSON-LD) ──
        self._parse_json_ld(soup, base_url, seen_urls)
        
        # ── 10. CSS Backgrounds (inline + style tags) ──
        self._parse_css_backgrounds(raw_html, base_url, seen_urls)
        
        # ── 11. Lazy-Load Attributes ──
        self._parse_lazy_attributes(soup, base_url, seen_urls)
        if rendered_soup:
            self._parse_lazy_attributes(rendered_soup, base_url, seen_urls, source_suffix=" (rendered)")
        
        # ── 12. Srcset Parsing ──
        self._parse_srcsets(soup, base_url, seen_urls)
        
        # ── 13. Inline Scripts (regex only) ──
        self._parse_inline_scripts(soup, base_url, seen_urls)
        
        # ── 14. HTML Comments ──
        self._parse_html_comments(soup, base_url, seen_urls)
        
        # ── 15. Link preload/prefetch ──
        self._parse_link_preload(soup, base_url, seen_urls)
    
    # ══════════════════════════════════════════
    #  PARSERS
    # ══════════════════════════════════════════
    
    def _parse_img_tags(self, soup, base_url, seen, source_suffix=""):
        """Parse semua <img> tags"""
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if not src or src.startswith('data:'):
                continue
            
            full_url = URLUtils.normalize(src, base_url)
            if full_url and full_url not in seen:
                seen.add(full_url)
                self.add_finding(
                    category='media',
                    subcategory='image',
                    url=full_url,
                    data={
                        'alt': img.get('alt', ''),
                        'title': img.get('title', ''),
                        'width': img.get('width', ''),
                        'height': img.get('height', ''),
                        'class': ' '.join(img.get('class', [])),
                        'loading': img.get('loading', ''),
                        'srcset': img.get('srcset', ''),
                    },
                    confidence=0.95,
                    source=f'<img> tag{source_suffix}',
                )
    
    def _parse_picture_tags(self, soup, base_url, seen):
        """Parse <picture> dan <source> tags"""
        for picture in soup.find_all('picture'):
            for source in picture.find_all('source'):
                srcset = source.get('srcset', '')
                if srcset:
                    # Parse srcset entries
                    for entry in PatternMatcher.SRCSET_ENTRY.finditer(srcset):
                        src = entry.group(1)
                        full_url = URLUtils.normalize(src, base_url)
                        if full_url and full_url not in seen:
                            seen.add(full_url)
                            self.add_finding(
                                category='media',
                                subcategory='image',
                                url=full_url,
                                data={
                                    'media': source.get('media', ''),
                                    'type': source.get('type', ''),
                                    'descriptor': entry.group(2),
                                },
                                confidence=0.95,
                                source='<picture><source> tag',
                            )
                    
                    # Juga coba parse tanpa descriptor
                    for src in srcset.split(','):
                        src = src.strip().split()[0]
                        if src and not src.startswith('data:'):
                            full_url = URLUtils.normalize(src, base_url)
                            if full_url and full_url not in seen:
                                seen.add(full_url)
                                self.add_finding(
                                    category='media',
                                    subcategory='image',
                                    url=full_url,
                                    confidence=0.9,
                                    source='<picture> srcset',
                                )
    
    def _parse_video_tags(self, soup, base_url, seen, source_suffix=""):
        """Parse <video> dan nested <source> tags"""
        for video in soup.find_all('video'):
            # Video src
            src = video.get('src', '')
            poster = video.get('poster', '')
            
            if src and not src.startswith('data:'):
                full_url = URLUtils.normalize(src, base_url)
                if full_url and full_url not in seen:
                    seen.add(full_url)
                    self.add_finding(
                        category='media',
                        subcategory='video',
                        url=full_url,
                        data={
                            'poster': URLUtils.normalize(poster, base_url) if poster else '',
                            'autoplay': video.has_attr('autoplay'),
                            'controls': video.has_attr('controls'),
                            'muted': video.has_attr('muted'),
                            'loop': video.has_attr('loop'),
                            'preload': video.get('preload', ''),
                            'width': video.get('width', ''),
                            'height': video.get('height', ''),
                        },
                        confidence=1.0,
                        source=f'<video> tag{source_suffix}',
                    )
            
            # Poster as image
            if poster and not poster.startswith('data:'):
                poster_url = URLUtils.normalize(poster, base_url)
                if poster_url and poster_url not in seen:
                    seen.add(poster_url)
                    self.add_finding(
                        category='media',
                        subcategory='image',
                        url=poster_url,
                        confidence=0.95,
                        source=f'<video poster>{source_suffix}',
                    )
            
            # Nested <source> tags
            for source in video.find_all('source'):
                s_src = source.get('src', '')
                if s_src and not s_src.startswith('data:'):
                    full_url = URLUtils.normalize(s_src, base_url)
                    if full_url and full_url not in seen:
                        seen.add(full_url)
                        
                        media_sub = 'video'
                        if MediaTypes.is_streaming_url(full_url):
                            media_sub = 'streaming'
                        
                        self.add_finding(
                            category='media',
                            subcategory=media_sub,
                            url=full_url,
                            data={
                                'type': source.get('type', ''),
                                'label': source.get('label', ''),
                                'res': source.get('res', ''),
                                'size': source.get('size', ''),
                            },
                            confidence=1.0,
                            source=f'<video><source>{source_suffix}',
                        )
    
    def _parse_audio_tags(self, soup, base_url, seen):
        """Parse <audio> tags"""
        for audio in soup.find_all('audio'):
            src = audio.get('src', '')
            if src and not src.startswith('data:'):
                full_url = URLUtils.normalize(src, base_url)
                if full_url and full_url not in seen:
                    seen.add(full_url)
                    self.add_finding(
                        category='media',
                        subcategory='audio',
                        url=full_url,
                        data={
                            'controls': audio.has_attr('controls'),
                            'autoplay': audio.has_attr('autoplay'),
                            'preload': audio.get('preload', ''),
                        },
                        confidence=1.0,
                        source='<audio> tag',
                    )
            
            for source in audio.find_all('source'):
                s_src = source.get('src', '')
                if s_src and not s_src.startswith('data:'):
                    full_url = URLUtils.normalize(s_src, base_url)
                    if full_url and full_url not in seen:
                        seen.add(full_url)
                        self.add_finding(
                            category='media',
                            subcategory='audio',
                            url=full_url,
                            data={'type': source.get('type', '')},
                            confidence=1.0,
                            source='<audio><source> tag',
                        )
    
    def _parse_iframe_tags(self, soup, base_url, seen, source_suffix=""):
        """Parse <iframe> tags — sering embed video player"""
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '') or iframe.get('data-src', '')
            if not src or src.startswith('data:') or src.startswith('about:'):
                continue
            
            full_url = URLUtils.normalize(src, base_url)
            if full_url and full_url not in seen:
                seen.add(full_url)
                
                # Detect known video embeds
                embed_type = self._identify_embed(full_url)
                
                self.add_finding(
                    category='media',
                    subcategory='iframe_embed',
                    url=full_url,
                    data={
                        'width': iframe.get('width', ''),
                        'height': iframe.get('height', ''),
                        'allow': iframe.get('allow', ''),
                        'sandbox': iframe.get('sandbox', ''),
                        'embed_type': embed_type,
                        'allowfullscreen': iframe.has_attr('allowfullscreen'),
                    },
                    confidence=0.85,
                    source=f'<iframe>{source_suffix}',
                )
    
    def _identify_embed(self, url: str) -> str:
        """Identifikasi tipe embed dari URL"""
        url_lower = url.lower()
        embeds = {
            'youtube': ['youtube.com/embed', 'youtube-nocookie.com/embed', 'youtu.be'],
            'vimeo': ['player.vimeo.com'],
            'dailymotion': ['dailymotion.com/embed'],
            'twitch': ['player.twitch.tv', 'twitch.tv/embed'],
            'facebook': ['facebook.com/plugins/video', 'fb.com/plugins'],
            'instagram': ['instagram.com/p/', 'instagram.com/reel/'],
            'tiktok': ['tiktok.com/embed'],
            'twitter': ['platform.twitter.com', 'twitframe.com'],
            'spotify': ['open.spotify.com/embed'],
            'soundcloud': ['w.soundcloud.com'],
            'google_drive': ['drive.google.com/file'],
            'google_maps': ['google.com/maps/embed'],
            'streamable': ['streamable.com/e/'],
            'vidio': ['vidio.com/embed'],
        }
        
        for embed_name, indicators in embeds.items():
            if any(ind in url_lower for ind in indicators):
                return embed_name
        return 'unknown'
    
    def _parse_embed_object(self, soup, base_url, seen):
        """Parse <embed> dan <object> tags"""
        for embed in soup.find_all('embed'):
            src = embed.get('src', '')
            if src and not src.startswith('data:'):
                full_url = URLUtils.normalize(src, base_url)
                if full_url and full_url not in seen:
                    seen.add(full_url)
                    self.add_finding(
                        category='media',
                        subcategory='embed',
                        url=full_url,
                        data={
                            'type': embed.get('type', ''),
                            'width': embed.get('width', ''),
                            'height': embed.get('height', ''),
                        },
                        confidence=0.85,
                        source='<embed> tag',
                    )
        
        for obj in soup.find_all('object'):
            data = obj.get('data', '')
            if data and not data.startswith('data:'):
                full_url = URLUtils.normalize(data, base_url)
                if full_url and full_url not in seen:
                    seen.add(full_url)
                    self.add_finding(
                        category='media',
                        subcategory='object',
                        url=full_url,
                        data={'type': obj.get('type', '')},
                        confidence=0.8,
                        source='<object> tag',
                    )
    
    def _parse_media_links(self, soup, base_url, seen):
        """Parse <a> links ke file media"""
        for a in soup.find_all('a', href=True):
            href = a['href']
            full_url = URLUtils.normalize(href, base_url)
            
            if full_url and full_url not in seen:
                media_type = MediaTypes.identify_type(url=full_url)
                if media_type != 'unknown':
                    seen.add(full_url)
                    self.add_finding(
                        category='media',
                        subcategory=media_type,
                        url=full_url,
                        data={
                            'link_text': a.get_text(strip=True)[:200],
                            'download': a.get('download', ''),
                            'rel': ' '.join(a.get('rel', [])),
                            'title': a.get('title', ''),
                        },
                        confidence=0.9,
                        source='<a href> link',
                    )
    
    def _parse_meta_tags(self, soup, base_url, seen):
        """Parse OpenGraph, Twitter Cards, dan meta media"""
        media_meta_props = [
            # OpenGraph
            'og:image', 'og:image:url', 'og:image:secure_url',
            'og:video', 'og:video:url', 'og:video:secure_url',
            'og:audio', 'og:audio:url', 'og:audio:secure_url',
            # Twitter
            'twitter:image', 'twitter:image:src',
            'twitter:player', 'twitter:player:stream',
            # Other
            'thumbnail', 'image_src',
        ]
        
        for meta in soup.find_all('meta'):
            prop = (
                meta.get('property', '') or 
                meta.get('name', '') or 
                meta.get('itemprop', '')
            ).lower()
            content = meta.get('content', '')
            
            if not content:
                continue
            
            if prop in media_meta_props:
                full_url = URLUtils.normalize(content, base_url)
                if full_url and full_url not in seen:
                    seen.add(full_url)
                    
                    media_sub = 'image'
                    if 'video' in prop:
                        media_sub = 'video'
                    elif 'audio' in prop:
                        media_sub = 'audio'
                    elif 'player' in prop:
                        media_sub = 'player_embed'
                    
                    self.add_finding(
                        category='media',
                        subcategory=media_sub,
                        url=full_url,
                        data={'meta_property': prop},
                        confidence=0.9,
                        source=f'<meta {prop}>',
                    )
            
            # Juga simpan metadata penting
            info_props = [
                'og:title', 'og:description', 'og:type', 'og:site_name',
                'og:image:width', 'og:image:height',
                'og:video:type', 'og:video:width', 'og:video:height',
                'twitter:title', 'twitter:description',
            ]
            if prop in info_props:
                self.add_finding(
                    category='info',
                    subcategory='meta',
                    data={'property': prop, 'content': content},
                    confidence=1.0,
                    source='<meta> tag',
                )
    
    def _parse_json_ld(self, soup, base_url, seen):
        """Parse JSON-LD structured data"""
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                text = script.string
                if not text:
                    continue
                
                data = json.loads(text)
                self._extract_urls_from_dict(
                    data, base_url, seen, 'JSON-LD'
                )
                
                # Store full JSON-LD
                self.add_finding(
                    category='info',
                    subcategory='json_ld',
                    data={'content': data},
                    confidence=1.0,
                    source='JSON-LD script',
                )
                
            except json.JSONDecodeError:
                continue
    
    def _extract_urls_from_dict(self, obj, base_url, seen, source, depth=0):
        """Rekursif extract URL dari nested dict/list (JSON-LD, config, etc)"""
        if depth > 10:
            return
        
        if isinstance(obj, str):
            if obj.startswith(('http://', 'https://', '/')):
                full_url = URLUtils.normalize(obj, base_url)
                if full_url and full_url not in seen:
                    media_type = MediaTypes.identify_type(url=full_url)
                    if media_type != 'unknown':
                        seen.add(full_url)
                        self.add_finding(
                            category='media',
                            subcategory=media_type,
                            url=full_url,
                            confidence=0.75,
                            source=source,
                        )
        
        elif isinstance(obj, dict):
            # Cek known media keys
            media_keys = [
                'url', 'contentUrl', 'embedUrl', 'thumbnailUrl',
                'image', 'video', 'audio', 'src', 'file',
                'poster', 'thumbnail', 'logo', 'photo',
                'downloadUrl', 'streamUrl', 'manifestUrl',
            ]
            for key, value in obj.items():
                if key in media_keys and isinstance(value, str):
                    full_url = URLUtils.normalize(value, base_url)
                    if full_url and full_url not in seen:
                        seen.add(full_url)
                        self.add_finding(
                            category='media',
                            subcategory=MediaTypes.identify_type(url=full_url) or 'unknown',
                            url=full_url,
                            data={'json_key': key},
                            confidence=0.85,
                            source=f'{source} [{key}]',
                        )
                else:
                    self._extract_urls_from_dict(
                        value, base_url, seen, source, depth + 1
                    )
        
        elif isinstance(obj, list):
            for item in obj:
                self._extract_urls_from_dict(
                    item, base_url, seen, source, depth + 1
                )
    
    def _parse_css_backgrounds(self, html, base_url, seen):
        """Parse CSS url() di inline styles dan <style> tags"""
        for match in PatternMatcher.CSS_URL.finditer(html):
            url = match.group(1).strip()
            if url.startswith('data:') or url.startswith('#'):
                continue
            
            full_url = URLUtils.normalize(url, base_url)
            if full_url and full_url not in seen:
                media_type = MediaTypes.identify_type(url=full_url)
                if media_type != 'unknown':
                    seen.add(full_url)
                    self.add_finding(
                        category='media',
                        subcategory=media_type,
                        url=full_url,
                        confidence=0.8,
                        source='CSS url()',
                        context=html[max(0, match.start()-50):match.end()+50],
                    )
    
    def _parse_lazy_attributes(self, soup, base_url, seen, source_suffix=""):
        """Parse lazy-load data attributes"""
        for attr_name in PatternMatcher.LAZY_LOAD_ATTRS:
            for tag in soup.find_all(attrs={attr_name: True}):
                value = tag[attr_name]
                if not value or value.startswith('data:'):
                    continue
                
                full_url = URLUtils.normalize(value, base_url)
                if full_url and full_url not in seen:
                    seen.add(full_url)
                    self.add_finding(
                        category='media',
                        subcategory=MediaTypes.identify_type(url=full_url) or 'image',
                        url=full_url,
                        data={
                            'tag': tag.name,
                            'attribute': attr_name,
                            'class': ' '.join(tag.get('class', [])),
                        },
                        confidence=0.9,
                        source=f'lazy-load [{attr_name}]{source_suffix}',
                    )
    
    def _parse_srcsets(self, soup, base_url, seen):
        """Parse semua srcset attributes"""
        for tag in soup.find_all(attrs={'srcset': True}):
            srcset = tag['srcset']
            for entry in srcset.split(','):
                parts = entry.strip().split()
                if parts:
                    src = parts[0]
                    descriptor = parts[1] if len(parts) > 1 else ''
                    
                    if src and not src.startswith('data:'):
                        full_url = URLUtils.normalize(src, base_url)
                        if full_url and full_url not in seen:
                            seen.add(full_url)
                            self.add_finding(
                                category='media',
                                subcategory='image',
                                url=full_url,
                                data={
                                    'descriptor': descriptor,
                                    'tag': tag.name,
                                },
                                confidence=0.9,
                                source='srcset attribute',
                            )
    
    def _parse_inline_scripts(self, soup, base_url, seen):
        """Parse inline <script> tags dengan regex"""
        for script in soup.find_all('script'):
            # Skip external scripts (punya src) dan JSON-LD
            if script.get('src') or script.get('type') == 'application/ld+json':
                continue
            
            text = script.string
            if not text or len(text) < 20:
                continue
            
            # Gunakan PatternMatcher
            results = PatternMatcher.scan_text(text, base_url)
            
            for category, matches in results.items():
                for match in matches:
                    if match.url not in seen:
                        seen.add(match.url)
                        
                        is_media = MediaTypes.is_media_url(match.url)
                        is_streaming = MediaTypes.is_streaming_url(match.url)
                        
                        if is_streaming:
                            self.add_finding(
                                category='media',
                                subcategory='streaming',
                                url=match.url,
                                confidence=match.confidence,
                                source=f'inline <script> [{match.pattern_name}]',
                                context=match.context,
                            )
                        elif is_media:
                            self.add_finding(
                                category='media',
                                subcategory=MediaTypes.identify_type(url=match.url),
                                url=match.url,
                                confidence=match.confidence,
                                source=f'inline <script> [{match.pattern_name}]',
                                context=match.context,
                            )
                        elif category in ('fetch_calls', 'api_endpoints'):
                            self.add_finding(
                                category='api',
                                subcategory='endpoint',
                                url=match.url,
                                confidence=match.confidence,
                                source=f'inline <script> [{match.pattern_name}]',
                                context=match.context,
                            )
    
    def _parse_html_comments(self, soup, base_url, seen):
        """Parse HTML comments — sering mengandung URL debug/staging"""
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            text = str(comment)
            if len(text) < 10:
                continue
            
            # Cari URL di comment
            matches = PatternMatcher.find_media_urls_only(text, base_url)
            for match in matches:
                if match.url not in seen:
                    seen.add(match.url)
                    self.add_finding(
                        category='media',
                        subcategory=MediaTypes.identify_type(url=match.url),
                        url=match.url,
                        confidence=0.6,  # lower confidence — dari comment
                        source='HTML comment',
                        context=text[:200],
                    )
    
    def _parse_link_preload(self, soup, base_url, seen):
        """Parse <link rel=preload/prefetch> — hint media yang akan dimuat"""
        for link in soup.find_all('link'):
            rel = ' '.join(link.get('rel', []))
            href = link.get('href', '')
            as_type = link.get('as', '')
            
            if not href or href.startswith('data:'):
                continue
            
            if rel in ('preload', 'prefetch', 'preconnect', 'dns-prefetch'):
                full_url = URLUtils.normalize(href, base_url)
                if full_url and full_url not in seen:
                    media_type = MediaTypes.identify_type(url=full_url)
                    if media_type != 'unknown' or as_type in ('image', 'video', 'audio', 'font'):
                        seen.add(full_url)
                        self.add_finding(
                            category='media',
                            subcategory=media_type or as_type,
                            url=full_url,
                            data={
                                'rel': rel,
                                'as': as_type,
                                'type': link.get('type', ''),
                                'crossorigin': link.get('crossorigin', ''),
                            },
                            confidence=0.85,
                            source=f'<link rel="{rel}">',
                        )