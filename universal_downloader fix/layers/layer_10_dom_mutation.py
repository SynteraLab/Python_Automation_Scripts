"""
layer_10_dom_mutation.py — DOM Mutation Deep Analysis

- Analyze DOM changes captured by MutationObserver
- Detect dynamically injected media elements
- Detect shadow DOM media
- Detect lazy-loaded components
- Detect dynamically created iframes
- Compare raw HTML vs rendered HTML
"""

import re
import logging
from typing import Dict, List, Set
from bs4 import BeautifulSoup

from layers.base import BaseLayer
from core.browser import BrowserCaptureResult, DOMChange
from core.session import SessionManager
from utils.media_types import MediaTypes
from utils.url_utils import URLUtils
from utils.pattern_matcher import PatternMatcher

logger = logging.getLogger(__name__)


class DOMMutationAnalysisLayer(BaseLayer):

    LAYER_NAME = "layer_10_dom_mutation"
    LAYER_DESCRIPTION = "DOM Mutation & Dynamic Content Analysis"

    async def execute(self, url, recon, capture, session):
        seen: Set[str] = set()
        base_url = url

        if not capture:
            self.add_error("No browser capture data")
            return

        # ── 1. Analyze captured DOM mutations ──
        self._analyze_mutations(capture, base_url, seen)

        # ── 2. Compare raw HTML vs rendered HTML ──
        raw_html = self._result.raw_data.get('raw_html')
        if raw_html is None:
            resp = session.get(url, timeout=10)
            raw_html = resp.body if resp else ''

        self._compare_raw_vs_rendered(
            raw_html, capture.page_html, base_url, seen
        )

        # ── 3. Detect shadow DOM ──
        self._detect_shadow_dom(capture, base_url)

        # ── 4. Detect lazy components ──
        self._detect_lazy_components(capture, base_url, seen)

        # ── 5. Detect dynamic iframes ──
        self._detect_dynamic_iframes(
            raw_html, capture.page_html, base_url, seen
        )

    def _analyze_mutations(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Analyze DOM mutations for media elements"""
        if not capture.dom_changes:
            return

        mutations_by_type = {
            'added': [],
            'modified': [],
        }

        for change in capture.dom_changes:
            full_url = URLUtils.normalize(change.value, base_url)
            if not full_url or full_url in seen:
                continue

            seen.add(full_url)
            media_type = MediaTypes.identify_type(url=full_url)

            mutation_info = {
                'url': full_url,
                'media_type': media_type,
                'tag': change.tag,
                'attribute': change.attribute,
                'change_type': change.change_type,
            }

            mutations_by_type[change.change_type].append(mutation_info)

            if media_type != 'unknown':
                self.add_finding(
                    category='media',
                    subcategory=media_type,
                    url=full_url,
                    data={
                        'dom_tag': change.tag,
                        'dom_attribute': change.attribute,
                        'mutation_type': change.change_type,
                        'injected_dynamically': True,
                    },
                    confidence=0.9,
                    source=f'DOM mutation ({change.change_type}): <{change.tag} {change.attribute}>',
                )

        self.add_finding(
            category='info',
            subcategory='dom_mutations_summary',
            data={
                'total_mutations': len(capture.dom_changes),
                'added_elements': len(mutations_by_type['added']),
                'modified_elements': len(mutations_by_type['modified']),
                'mutations': mutations_by_type,
            },
            confidence=1.0,
            source='DOM mutation analysis',
        )

    def _compare_raw_vs_rendered(
        self, raw_html: str, rendered_html: str,
        base_url: str, seen: Set[str]
    ):
        """Compare raw HTML vs rendered HTML to find JS-injected media"""
        if not raw_html or not rendered_html:
            return

        raw_soup = BeautifulSoup(raw_html, 'lxml')
        rendered_soup = BeautifulSoup(rendered_html, 'lxml')

        # Count media elements
        media_tags = ['img', 'video', 'audio', 'source', 'iframe', 'embed']

        raw_counts = {}
        rendered_counts = {}

        for tag in media_tags:
            raw_counts[tag] = len(raw_soup.find_all(tag))
            rendered_counts[tag] = len(rendered_soup.find_all(tag))

        # Find elements only in rendered (JS-injected)
        raw_srcs = set()
        for tag_name in media_tags:
            for el in raw_soup.find_all(tag_name):
                src = el.get('src', '') or el.get('data-src', '')
                if src:
                    raw_srcs.add(URLUtils.normalize(src, base_url))

        js_injected = []
        for tag_name in media_tags:
            for el in rendered_soup.find_all(tag_name):
                src = el.get('src', '') or el.get('data-src', '')
                if not src:
                    continue
                full_url = URLUtils.normalize(src, base_url)
                if full_url and full_url not in raw_srcs and full_url not in seen:
                    seen.add(full_url)
                    media_type = MediaTypes.identify_type(url=full_url)

                    js_injected.append({
                        'url': full_url,
                        'tag': tag_name,
                        'media_type': media_type,
                    })

                    if media_type != 'unknown':
                        self.add_finding(
                            category='media',
                            subcategory=media_type,
                            url=full_url,
                            data={
                                'tag': tag_name,
                                'injected_by_js': True,
                                'not_in_raw_html': True,
                            },
                            confidence=0.9,
                            source=f'JS-injected <{tag_name}> (not in raw HTML)',
                        )

        self.add_finding(
            category='info',
            subcategory='raw_vs_rendered',
            data={
                'raw_element_counts': raw_counts,
                'rendered_element_counts': rendered_counts,
                'js_injected_media': len(js_injected),
                'js_injected_details': js_injected[:20],
                'difference': {
                    tag: rendered_counts.get(tag, 0) - raw_counts.get(tag, 0)
                    for tag in media_tags
                    if rendered_counts.get(tag, 0) != raw_counts.get(tag, 0)
                },
            },
            confidence=1.0,
            source='raw vs rendered comparison',
        )

    def _detect_shadow_dom(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Detect shadow DOM usage"""
        if not capture.page_html:
            return

        shadow_indicators = [
            'attachShadow', 'shadowRoot', 'shadow-root',
            ':host', '::slotted', '<slot', 'createShadowRoot',
        ]

        found_indicators = [
            ind for ind in shadow_indicators
            if ind.lower() in capture.page_html.lower()
        ]

        if found_indicators:
            self.add_finding(
                category='info',
                subcategory='shadow_dom',
                data={
                    'detected': True,
                    'indicators': found_indicators,
                    'note': (
                        'Shadow DOM detected. Media elements inside shadow DOM '
                        'may not be visible to standard selectors. '
                        'Browser-based extraction recommended.'
                    ),
                },
                confidence=0.8,
                source='Shadow DOM detection',
            )

    def _detect_lazy_components(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Detect lazy-loaded components/modules"""
        if not capture.page_html:
            return

        html = capture.page_html
        lazy_indicators = []

        # React lazy
        if 'React.lazy' in html or 'lazy(' in html:
            lazy_indicators.append('React.lazy')

        # Vue async components
        if 'defineAsyncComponent' in html or 'import(' in html:
            lazy_indicators.append('Vue/generic async import')

        # Angular lazy modules
        if 'loadChildren' in html:
            lazy_indicators.append('Angular lazy module')

        # Intersection Observer
        if 'IntersectionObserver' in html:
            lazy_indicators.append('IntersectionObserver')

        # Loading="lazy"
        soup = BeautifulSoup(html, 'lxml')
        lazy_elements = soup.find_all(attrs={'loading': 'lazy'})

        if lazy_indicators or lazy_elements:
            self.add_finding(
                category='info',
                subcategory='lazy_loading',
                data={
                    'techniques': lazy_indicators,
                    'native_lazy_elements': len(lazy_elements),
                    'note': (
                        'Lazy loading detected. Auto-scroll was used '
                        'to trigger loading. Some content may require '
                        'additional scrolling or interaction.'
                    ),
                },
                confidence=0.85,
                source='lazy component detection',
            )

    def _detect_dynamic_iframes(
        self, raw_html: str, rendered_html: str,
        base_url: str, seen: Set[str]
    ):
        """Detect iframes that were dynamically created"""
        if not raw_html or not rendered_html:
            return

        raw_soup = BeautifulSoup(raw_html, 'lxml')
        rendered_soup = BeautifulSoup(rendered_html, 'lxml')

        raw_iframes = set()
        for iframe in raw_soup.find_all('iframe'):
            src = iframe.get('src', '') or iframe.get('data-src', '')
            if src:
                raw_iframes.add(URLUtils.normalize(src, base_url))

        dynamic_iframes = []
        for iframe in rendered_soup.find_all('iframe'):
            src = iframe.get('src', '') or iframe.get('data-src', '')
            if not src:
                continue
            full_url = URLUtils.normalize(src, base_url)
            if full_url and full_url not in raw_iframes and full_url not in seen:
                seen.add(full_url)
                dynamic_iframes.append({
                    'url': full_url,
                    'width': iframe.get('width', ''),
                    'height': iframe.get('height', ''),
                    'allow': iframe.get('allow', ''),
                })

                self.add_finding(
                    category='media',
                    subcategory='dynamic_iframe',
                    url=full_url,
                    data={
                        'injected_by_js': True,
                        'width': iframe.get('width', ''),
                        'height': iframe.get('height', ''),
                    },
                    confidence=0.85,
                    source='dynamic iframe detection',
                )

        if dynamic_iframes:
            self.add_finding(
                category='info',
                subcategory='dynamic_iframes',
                data={
                    'count': len(dynamic_iframes),
                    'iframes': dynamic_iframes,
                },
                confidence=1.0,
                source='dynamic iframe analysis',
            )