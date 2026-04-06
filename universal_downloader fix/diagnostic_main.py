"""
main.py — Media Diagnostic Tool
Entry point utama. Jalankan dari terminal.

Usage:
    python main.py https://example.com/video/123
    python main.py --url https://example.com --headless false
    python main.py --url https://example.com --output ./reports
"""

import sys
import asyncio
import argparse
import logging
import json
import os
import time

# ── Setup path ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DiagnosticConfig, DEFAULT_CONFIG
from core.orchestrator import Orchestrator
from core.browser import BrowserEngine
from core.session import SessionManager
from report.generator import ReportGenerator

# ── Layers ──
from layers.layer_01_static import StaticAnalysisLayer
from layers.layer_02_dynamic import DynamicAnalysisLayer
from layers.layer_03_js_ast import JSAstAnalysisLayer
from layers.layer_04_api_probe import APIProbeLayer
from layers.layer_05_streaming import StreamingAnalysisLayer
from layers.layer_06_websocket import WebSocketAnalysisLayer
from layers.layer_07_service_worker import ServiceWorkerAnalysisLayer
from layers.layer_08_infrastructure import InfrastructureAnalysisLayer
from layers.layer_09_auth_flow import AuthFlowAnalysisLayer
from layers.layer_10_dom_mutation import DOMMutationAnalysisLayer
from layers.layer_11_multi_server import MultiServerLayer



# ══════════════════════════════════════════════
#  LOGGING SETUP
# ══════════════════════════════════════════════

def setup_logging(verbose: bool = False):
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format='%(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )

    # Suppress noisy libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('playwright').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)


# ══════════════════════════════════════════════
#  MAIN DIAGNOSTIC FUNCTION
# ══════════════════════════════════════════════

async def run_diagnostic(url: str, config: DiagnosticConfig | None = None):
    """
    Jalankan full diagnostic pada URL target.

    Returns:
        Tuple[DiagnosticResult, Dict]: (result, report)
    """
    config = config or DEFAULT_CONFIG
    config.ensure_output_dir()

    # ── Create orchestrator ──
    orchestrator = Orchestrator(config)

    # ── Register ALL layers ──
    orchestrator.register_layer(
        'layer_01_static', StaticAnalysisLayer(config)
    )
    orchestrator.register_layer(
        'layer_02_dynamic', DynamicAnalysisLayer(config)
    )
    orchestrator.register_layer(
        'layer_03_js_ast', JSAstAnalysisLayer(config)
    )
    orchestrator.register_layer(
        'layer_04_api_probe', APIProbeLayer(config)
    )
    orchestrator.register_layer(
        'layer_05_streaming', StreamingAnalysisLayer(config)
    )
    orchestrator.register_layer(
        'layer_06_websocket', WebSocketAnalysisLayer(config)
    )
    orchestrator.register_layer(
        'layer_07_service_worker', ServiceWorkerAnalysisLayer(config)
    )
    orchestrator.register_layer(
        'layer_08_infrastructure', InfrastructureAnalysisLayer(config)
    )
    orchestrator.register_layer(
        'layer_09_auth_flow', AuthFlowAnalysisLayer(config)
    )
    orchestrator.register_layer(
        'layer_10_dom_mutation', DOMMutationAnalysisLayer(config)
    )
    orchestrator.register_layer(
        'layer_11_multi_server', MultiServerLayer(config)
    )


    # ── Run diagnostic ──
    result = await orchestrator.diagnose(url)

    # ── Generate prompt-ready report ──
    generator = ReportGenerator(config)
    report = generator.generate(result)
    json_path, txt_path = generator.save(report, config.report.output_dir)

    # ── Print final output ──
    def _box_line(label: str, value: str) -> None:
        text = str(value)
        if len(text) > 39:
            text = text[:36] + "..."
        content = f"  {label:<11} {text}"
        print(f"║{content:<54}║")

    print("\n")
    print("╔══════════════════════════════════════════════════════╗")
    print("║         📋 DIAGNOSTIC COMPLETE                      ║")
    print("╠══════════════════════════════════════════════════════╣")

    media_count = report['media_found']['total_count']
    api_count = report['api_endpoints']['total_count']
    streaming = report.get('streaming', {})
    s_text = 'Yes (DRM!)' if streaming.get('has_drm') else 'Yes' if streaming.get('has_streaming') else 'No'
    strat = report.get('extraction_strategy', {})
    c_text = str(strat.get('estimated_complexity', 'unknown'))

    _box_line('Target:', url)
    _box_line('Duration:', f"{result.duration:.1f}s")
    _box_line('Media:', f"{media_count} items found")
    _box_line('APIs:', f"{api_count} endpoints")
    _box_line('Streaming:', s_text)
    _box_line('Complexity:', c_text)

    print("╠══════════════════════════════════════════════════════╣")
    _box_line('Full Report:', json_path)
    _box_line('Summary:', txt_path)
    print("╠══════════════════════════════════════════════════════╣")
    print("║                                                      ║")
    print("║  💡 NEXT STEP:                                       ║")
    print("║  Copy the JSON report and use it as prompt:          ║")
    print("║                                                      ║")
    print('║  "Buatkan extractor berdasarkan diagnosis ini:       ║')
    print('║   [paste isi JSON report]"                           ║')
    print("║                                                      ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    return result, report


# ══════════════════════════════════════════════
#  CLI ARGUMENT PARSER
# ══════════════════════════════════════════════

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="🔬 Media Diagnostic Tool — Diagnosa file media tersembunyi di website",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py https://example.com/video/123
  python main.py --url https://example.com --output ./my_reports
  python main.py --url https://example.com --no-headless --verbose
  python main.py --url https://example.com --proxy http://127.0.0.1:8080
        """
    )

    parser.add_argument(
        'url', nargs='?', default=None,
        help='Target URL to diagnose'
    )
    parser.add_argument(
        '--url', '-u', dest='url_flag', default=None,
        help='Target URL (alternative to positional)'
    )
    parser.add_argument(
        '--output', '-o', default='diagnostic_output',
        help='Output directory (default: diagnostic_output)'
    )
    parser.add_argument(
        '--no-headless', action='store_true',
        help='Show browser window (useful for debugging)'
    )
    parser.add_argument(
        '--no-screenshot', action='store_true',
        help='Skip taking screenshots'
    )
    parser.add_argument(
        '--no-stealth', action='store_true',
        help='Disable stealth mode'
    )
    parser.add_argument(
        '--proxy', '-p', default=None,
        help='Proxy server (e.g., http://127.0.0.1:8080)'
    )
    parser.add_argument(
        '--timeout', '-t', type=int, default=300,
        help='Max total time in seconds (default: 300)'
    )
    parser.add_argument(
        '--max-scroll', type=int, default=20,
        help='Maximum scroll count (default: 20)'
    )
    parser.add_argument(
        '--max-js', type=int, default=50,
        help='Maximum JS files to analyze (default: 50)'
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='Verbose/debug output'
    )

    return parser.parse_args()


# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════

def main():
    """Main entry point"""
    args = parse_args()

    # Determine URL
    target_url = args.url or args.url_flag

    if not target_url:
        # Interactive mode
        print()
        print("╔══════════════════════════════════════════════╗")
        print("║   🔬 MEDIA DIAGNOSTIC TOOL                  ║")
        print("║   Diagnosa file media tersembunyi di website ║")
        print("╚══════════════════════════════════════════════╝")
        print()
        target_url = input("  🎯 Masukkan URL target: ").strip()

        if not target_url:
            print("  ❌ URL tidak boleh kosong!")
            sys.exit(1)

    # Validate URL
    if not target_url.startswith(('http://', 'https://')):
        target_url = 'https://' + target_url

    # Setup logging
    setup_logging(verbose=args.verbose)

    # Build config
    config = DiagnosticConfig()
    config.report.output_dir = args.output
    config.browser.headless = not args.no_headless
    config.browser.stealth_mode = not args.no_stealth
    config.report.take_screenshots = not args.no_screenshot
    config.scan.max_total_time = args.timeout
    config.scan.max_scrolls = args.max_scroll
    config.scan.max_js_files = args.max_js

    if args.proxy:
        config.proxy = args.proxy

    # Run
    try:
        result, report = asyncio.run(run_diagnostic(target_url, config))

        # Exit code based on success
        if result.errors:
            sys.exit(1)
        sys.exit(0)

    except KeyboardInterrupt:
        print("\n\n  ⚠️ Diagnostic interrupted by user")
        sys.exit(130)

    except Exception as e:
        print(f"\n  ❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
