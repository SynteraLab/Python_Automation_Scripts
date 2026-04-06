"""
layer_06_websocket.py — WebSocket & SSE Deep Analysis

- Analyze captured WebSocket connections
- Parse WebSocket message payloads (JSON, binary)
- Detect media URLs in WebSocket messages
- Detect streaming protocols over WebSocket
- Identify WebSocket frameworks (socket.io, sockjs)
- Detect Server-Sent Events (SSE) endpoints
"""

import re
import json
import logging
from typing import Dict, List, Set, Optional
from urllib.parse import urlparse

from layers.base import BaseLayer
from core.browser import BrowserCaptureResult, CapturedWebSocket
from core.session import SessionManager
from utils.media_types import MediaTypes
from utils.url_utils import URLUtils
from utils.pattern_matcher import PatternMatcher

logger = logging.getLogger(__name__)


class WebSocketAnalysisLayer(BaseLayer):

    LAYER_NAME = "layer_06_websocket"
    LAYER_DESCRIPTION = "WebSocket & Server-Sent Events Analysis"

    async def execute(self, url, recon, capture, session):
        seen: Set[str] = set()
        base_url = url

        if not capture:
            self.add_error("No browser capture data")
            return

        # ── 1. Analyze captured WebSocket connections ──
        self._analyze_websocket_connections(capture, base_url, seen)

        # ── 2. Deep parse WebSocket messages ──
        self._parse_websocket_messages(capture, base_url, seen)

        # ── 3. Detect WebSocket frameworks ──
        self._detect_ws_frameworks(capture, base_url)

        # ── 4. Detect SSE endpoints from network ──
        self._detect_sse_endpoints(capture, base_url, seen)

        # ── 5. Scan HTML/JS for WebSocket URLs ──
        self._scan_for_ws_urls(capture, base_url, seen)

    def _analyze_websocket_connections(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Analyze each WebSocket connection"""
        for ws in capture.websockets:
            parsed = urlparse(ws.url)

            self.add_finding(
                category='info',
                subcategory='websocket_connection',
                url=ws.url,
                data={
                    'protocol': parsed.scheme,  # ws or wss
                    'host': parsed.netloc,
                    'path': parsed.path,
                    'messages_sent': sum(
                        1 for m in ws.messages
                        if m.get('direction') == 'sent'
                    ),
                    'messages_received': sum(
                        1 for m in ws.messages
                        if m.get('direction') == 'received'
                    ),
                    'total_messages': len(ws.messages),
                    'is_closed': ws.is_closed,
                    'is_same_domain': URLUtils.is_same_domain(
                        ws.url.replace('wss://', 'https://').replace('ws://', 'http://'),
                        base_url
                    ),
                },
                confidence=1.0,
                source='WebSocket connection analysis',
            )

    def _parse_websocket_messages(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Deep parse WebSocket message payloads"""
        for ws in capture.websockets:
            for msg in ws.messages:
                data = msg.get('data', '')
                direction = msg.get('direction', '')

                if not isinstance(data, str) or len(data) < 10:
                    continue

                # ── Try JSON parse ──
                json_data = None
                payload = data

                # socket.io format: "42/namespace,{json}"
                sio_match = re.match(r'^\d+(?:/[^,]*)?,(.+)$', data)
                if sio_match:
                    payload = sio_match.group(1)

                try:
                    json_data = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    # Try extracting JSON from mixed content
                    json_match = re.search(r'(\{.+\}|\[.+\])', payload)
                    if json_match:
                        try:
                            json_data = json.loads(json_match.group(1))
                        except (json.JSONDecodeError, ValueError):
                            pass

                if json_data:
                    self._extract_urls_from_ws_json(
                        json_data, ws.url, base_url, seen, direction
                    )
                else:
                    # Regex scan raw text
                    media_matches = PatternMatcher.find_media_urls_only(
                        data, base_url
                    )
                    for match in media_matches:
                        if match.url not in seen:
                            seen.add(match.url)
                            self.add_finding(
                                category='media',
                                subcategory=MediaTypes.identify_type(url=match.url),
                                url=match.url,
                                data={
                                    'websocket_url': ws.url,
                                    'direction': direction,
                                    'found_via': 'regex in WS message',
                                },
                                confidence=0.75,
                                source='WebSocket message regex',
                            )

    def _extract_urls_from_ws_json(
        self, data, ws_url: str, base_url: str,
        seen: Set[str], direction: str, path: str = "", depth: int = 0
    ):
        """Recursively extract URLs from WebSocket JSON payloads"""
        if depth > 10:
            return

        if isinstance(data, str):
            if data.startswith(('http://', 'https://', '//')):
                full_url = URLUtils.normalize(data, base_url)
                if full_url and full_url not in seen:
                    media_type = MediaTypes.identify_type(url=full_url)
                    if media_type != 'unknown':
                        seen.add(full_url)
                        self.add_finding(
                            category='media',
                            subcategory=media_type,
                            url=full_url,
                            data={
                                'websocket_url': ws_url,
                                'direction': direction,
                                'json_path': path,
                            },
                            confidence=0.85,
                            source=f'WebSocket JSON [{path}]',
                        )

        elif isinstance(data, dict):
            for key, value in data.items():
                new_path = f"{path}.{key}" if path else key
                self._extract_urls_from_ws_json(
                    value, ws_url, base_url, seen,
                    direction, new_path, depth + 1
                )

        elif isinstance(data, list):
            for i, item in enumerate(data[:50]):
                new_path = f"{path}[{i}]"
                self._extract_urls_from_ws_json(
                    item, ws_url, base_url, seen,
                    direction, new_path, depth + 1
                )

    def _detect_ws_frameworks(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Detect WebSocket framework (socket.io, sockjs, etc)"""
        frameworks_detected = set()

        for ws in capture.websockets:
            url_lower = ws.url.lower()

            framework_sigs = {
                'socket.io': ['socket.io', '/socket.io/'],
                'sockjs': ['sockjs', '/sockjs/'],
                'signalr': ['signalr', '/signalr/'],
                'actioncable': ['cable', '/cable'],
                'phoenix': ['phoenix', '/socket/websocket'],
                'pusher': ['pusher', 'ws.pusherapp.com'],
                'ably': ['ably', 'realtime.ably.io'],
                'firebase': ['firebaseio.com', 's-usc1c-nss'],
            }

            for fw_name, indicators in framework_sigs.items():
                if any(ind in url_lower for ind in indicators):
                    if fw_name not in frameworks_detected:
                        frameworks_detected.add(fw_name)
                        self.add_finding(
                            category='info',
                            subcategory='ws_framework',
                            url=ws.url,
                            data={
                                'framework': fw_name,
                                'websocket_url': ws.url,
                            },
                            confidence=0.9,
                            source=f'WebSocket framework: {fw_name}',
                        )

            # Check messages for framework signatures
            for msg in ws.messages[:10]:
                data = msg.get('data', '')
                # socket.io pattern: starts with digit
                if re.match(r'^\d{1,2}', data) and 'socket.io' not in frameworks_detected:
                    frameworks_detected.add('socket.io')
                    self.add_finding(
                        category='info',
                        subcategory='ws_framework',
                        url=ws.url,
                        data={'framework': 'socket.io', 'detected_via': 'message format'},
                        confidence=0.7,
                        source='WebSocket framework: socket.io (message pattern)',
                    )

    def _detect_sse_endpoints(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Detect Server-Sent Events endpoints"""
        if not capture:
            return

        for req in capture.requests:
            ct = (req.content_type or '').lower()

            if 'text/event-stream' in ct:
                self.add_finding(
                    category='info',
                    subcategory='sse_endpoint',
                    url=req.url,
                    data={
                        'content_type': req.content_type,
                        'status': req.status,
                        'method': req.method,
                    },
                    confidence=1.0,
                    source='SSE endpoint (text/event-stream)',
                )

                # Scan response body untuk media URLs
                if req.response_body:
                    matches = PatternMatcher.find_media_urls_only(
                        req.response_body, base_url
                    )
                    for match in matches:
                        if match.url not in seen:
                            seen.add(match.url)
                            self.add_finding(
                                category='media',
                                subcategory=MediaTypes.identify_type(url=match.url),
                                url=match.url,
                                data={'sse_endpoint': req.url},
                                confidence=0.8,
                                source='SSE response body',
                            )

    def _scan_for_ws_urls(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Scan HTML/JS for ws:// or wss:// URLs not yet connected"""
        if not capture or not capture.page_html:
            return

        ws_pattern = re.compile(
            r'''(?:["'])(wss?://[^\s"'<>]+)(?:["'])''',
            re.IGNORECASE
        )

        for match in ws_pattern.finditer(capture.page_html):
            ws_url = match.group(1)
            if ws_url not in seen:
                seen.add(ws_url)
                # Check if already connected
                connected = any(
                    ws.url == ws_url for ws in capture.websockets
                )
                self.add_finding(
                    category='info',
                    subcategory='ws_url_in_source',
                    url=ws_url,
                    data={
                        'was_connected': connected,
                        'found_in': 'page source',
                    },
                    confidence=0.8,
                    source='WebSocket URL in HTML/JS',
                )