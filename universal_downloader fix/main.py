#!/usr/bin/env python3
"""
Universal Media Downloader

Two modes:
  python main.py              → Interactive TUI menu
  python main.py download URL → CLI mode (direct command)

Smart download flow:
  1. Custom extractors (PubJav, SupJav, JWPlayer, HLS)
  2. yt-dlp fallback (YouTube, TikTok, Instagram, 1000+ sites)  
  3. Generic HTML extractor
"""

import sys

def main():
    # If no arguments or just flags → Interactive TUI
    if len(sys.argv) <= 1:
        try:
            from tui import run_tui
            run_tui()
        except ImportError as e:
            print(f"TUI requires 'rich' library: pip install rich")
            print(f"Or use CLI: python main.py download \"URL\"")
            print(f"Error: {e}")
            return 1
        return 0

    # If has subcommand → CLI mode
    from cli import main as cli_main
    return cli_main()


if __name__ == '__main__':
    sys.exit(main() or 0)
