#!/usr/bin/env python3
import argparse
import configparser
import html
import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

VERBOSE = False


def log(msg: str) -> None:
    if VERBOSE:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def parse_args():
    parser = argparse.ArgumentParser(description="Generate hierarchical HTML status page from Zabbix Services.")
    default_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "status_page.ini")
    parser.add_argument(
        "-c", "--config", 
        default=default_cfg, 
        help=f"Path to INI config file (default: {default_cfg})"
    )
    parser.add_argument(
        "-v", "--verbose", 
        action="store_true", 
        help="Enable detailed log output (silent by default for cron jobs)"
    )
    return parser.parse_args()


def load_config(config_path: str) -> configparser.ConfigParser:
    if not os.path.exists(config_path):
        print(f"Error: Configuration file not found at '{config_path}'", file=sys.stderr)
        sys.exit(1)
    
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf-8")
    return config


def fetch_zabbix_services(url: str, token: str, verify_ssl: bool = True) -> list:
    log(f"Connecting to Zabbix API at {url} (SSL verification: {verify_ssl})...")
    payload = {
        "jsonrpc": "2.0",
        "method": "service.get",
        "params": {
            "output": ["serviceid", "name", "status", "description"],
            "selectParents": ["serviceid"],
            "selectChildren": ["serviceid"],
            "sortfield": "name",
            "sortorder": "ASC"
        },
        "id": 1
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json-rpc",
            "Authorization": f"Bearer {token}"
        }
    )

    ctx = ssl.create_default_context()
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "error" in data:
                print(f"Zabbix API Error: {data['error']}", file=sys.stderr)
                sys.exit(1)
            services = data.get("result", [])
            log(f"Retrieved {len(services)} services from Zabbix.")
            return services
    except Exception as e:
        print(f"Failed to fetch data from Zabbix: {e}", file=sys.stderr)
        sys.exit(1)


def map_service_status(status_code: int) -> dict:
    if status_code in (-1, 0):
        return {
            "label": "Operational",
            "badge_class": "badge-ok",
            "dot_class": "dot-ok",
            "severity": 0
        }
    elif status_code in (1, 2):
        return {
            "label": "Degraded",
            "badge_class": "badge-warn",
            "dot_class": "dot-warn",
            "severity": 1
        }
    elif status_code in (3, 4):
        return {
            "label": "Partial Outage",
            "badge_class": "badge-avg",
            "dot_class": "dot-avg",
            "severity": 2
        }
    else:
        return {
            "label": "Major Outage",
            "badge_class": "badge-crit",
            "dot_class": "dot-crit",
            "severity": 3
        }


def build_service_tree(services: list) -> list:
    services_by_id = {s["serviceid"]: s for s in services}
    for s in services:
        s["children_objs"] = []

    for s in services:
        for child_ref in s.get("children", []):
            cid = child_ref.get("serviceid")
            if cid in services_by_id:
                s["children_objs"].append(services_by_id[cid])
        s["children_objs"].sort(key=lambda x: x.get("name", "").lower())

    root_services = [
        s for s in services
        if not any(p.get("serviceid") in services_by_id for p in s.get("parents", []))
    ]
    root_services.sort(key=lambda x: x.get("name", "").lower())
    log(f"Constructed service tree: {len(root_services)} top-level services.")
    return root_services


def has_descendant_issue(service: dict) -> bool:
    if int(service.get("status", -1)) not in (-1, 0):
        return True
    return any(has_descendant_issue(c) for c in service.get("children_objs", []))


def render_service_node(service: dict, level: int, expand_mode: str, visited: set = None) -> str:
    if visited is None:
        visited = set()
    sid = service.get("serviceid")
    if sid in visited:
        return ""
    visited.add(sid)

    status_val = int(service.get("status", -1))
    info = map_service_status(status_val)
    children = service.get("children_objs", [])
    has_children = len(children) > 0
    desc_line = f'<div class="service-desc">{service["description"]}</div>' if service.get("description") else ''

    if expand_mode == "always":
        is_open = "open"
    elif expand_mode in ("never", "collapsed"):
        is_open = ""
    else:
        is_open = "open" if has_descendant_issue(service) else ""

    chevron_svg = """<svg class="chevron" viewBox="0 0 20 20" fill="currentColor">
        <path fill-rule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clip-rule="evenodd"/>
    </svg>"""

    if not has_children:
        return f"""
        <div class="service-card level-{level}">
            <div class="service-left">
                <div class="service-titles">
                    <span class="service-name">{service['name']}</span>
                    {desc_line}
                </div>
            </div>
            <div class="badge {info['badge_class']}">
                <span class="dot {info['dot_class']}"></span>
                {info['label']}
            </div>
        </div>
        """
    else:
        children_html = "\n".join(
            render_service_node(c, level + 1, expand_mode, visited.copy())
            for c in children
        )
        return f"""
        <details class="service-group level-{level}" {is_open}>
            <summary class="service-card service-parent">
                <div class="service-left">
                    {chevron_svg}
                    <div class="service-titles">
                        <span class="service-name">{service['name']}</span>
                        {desc_line}
                    </div>
                </div>
                <div class="badge {info['badge_class']}">
                    <span class="dot {info['dot_class']}"></span>
                    {info['label']}
                </div>
            </summary>
            <div class="service-children">
                {children_html}
            </div>
        </details>
        """


def parse_inline_markdown(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^\*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^\*]+)\*", r"<em>\1</em>", text)
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s\)]+)\)",
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>',
        text
    )
    return text


def simple_markdown_to_html(md_text: str) -> str:
    lines = md_text.strip().splitlines()
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append('<ul class="notice-list">')
                in_list = True
            item_html = parse_inline_markdown(stripped[2:].strip())
            html_lines.append(f"<li>{item_html}</li>")
            continue
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False

        if not stripped:
            continue

        if stripped.startswith("### "):
            h_text = parse_inline_markdown(stripped[4:].strip())
            html_lines.append(f'<h4 class="notice-heading">{h_text}</h4>')
        elif stripped.startswith("## "):
            h_text = parse_inline_markdown(stripped[3:].strip())
            html_lines.append(f'<h3 class="notice-heading">{h_text}</h3>')
        elif stripped.startswith("# "):
            h_text = parse_inline_markdown(stripped[2:].strip())
            html_lines.append(f'<h2 class="notice-heading">{h_text}</h2>')
        elif stripped.startswith("> "):
            q_text = parse_inline_markdown(stripped[2:].strip())
            html_lines.append(f'<blockquote class="notice-quote">{q_text}</blockquote>')
        else:
            p_text = parse_inline_markdown(stripped)
            html_lines.append(f'<p class="notice-para">{p_text}</p>')

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def render_notice_card(filepath: str, default_style: str = "warning") -> str:
    if not filepath or not os.path.exists(filepath):
        log(f"Notice file not present ('{filepath}'). Skipping announcement banner.")
        return ""

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_content = f.read().strip()
    except Exception as e:
        print(f"Notice file read error ({filepath}): {e}", file=sys.stderr)
        return ""

    if not raw_content:
        log(f"Notice file '{filepath}' is empty. Skipping announcement banner.")
        return ""

    lines = raw_content.splitlines()
    meta = {}

    if lines and lines[0].strip() == "---":
        end_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx != -1:
            for l in lines[1:end_idx]:
                if ":" in l:
                    k, v = l.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            lines = lines[end_idx + 1:]

    while lines and lines[0].strip().startswith("<!--") and lines[0].strip().endswith("-->"):
        comment_inner = lines[0].strip()[4:-3].strip()
        parts = re.split(r"[,|]", comment_inner)
        for part in parts:
            if ":" in part:
                k, v = part.split(":", 1)
                meta[k.strip().lower()] = v.strip()
        lines = lines[1:]

    while lines and not lines[0].strip():
        lines.pop(0)

    custom_title = meta.get("title")
    if not custom_title and lines:
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("# ", "## ", "### ")):
                custom_title = re.sub(r"^#{1,3}\s+", "", stripped).strip()
                lines.pop(idx)
                break

    body_content = "\n".join(lines).strip()
    style = meta.get("style", meta.get("type", default_style)).lower()

    valid_styles = {
        "warning": {
            "card_class": "notice-warning",
            "badge_class": "badge-warn",
            "dot_class": "dot-warn",
            "default_title": "Scheduled Maintenance"
        },
        "info": {
            "card_class": "notice-info",
            "badge_class": "badge-info",
            "dot_class": "dot-info",
            "default_title": "Announcement"
        },
        "critical": {
            "card_class": "notice-critical",
            "badge_class": "badge-crit",
            "dot_class": "dot-crit",
            "default_title": "Active Incident Notice"
        }
    }

    style_cfg = valid_styles.get(style, valid_styles["warning"])
    box_title = custom_title if custom_title else style_cfg["default_title"]
    body_html = simple_markdown_to_html(body_content)

    log(f"Notice banner loaded from '{filepath}' (Title: '{box_title}', Style: '{style}')")

    return f"""
    <div class="notice-card {style_cfg['card_class']}">
        <div class="notice-header">
            <span class="badge {style_cfg['badge_class']}">
                <span class="dot {style_cfg['dot_class']}"></span>
                {html.escape(box_title)}
            </span>
        </div>
        <div class="notice-body">
            {body_html}
        </div>
    </div>
    """


def render_html(services: list, cfg: configparser.ConfigParser) -> str:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    title = cfg.get("general", "title", fallback="System Status")
    logo_url = cfg.get("general", "logo_url", fallback="").strip()
    refresh_sec = cfg.getint("general", "refresh_interval", fallback=60)
    expand_mode = cfg.get("general", "expand_mode", fallback="auto").strip().lower()
    support_url = cfg.get("general", "support_url", fallback="").strip()
    support_text = cfg.get("general", "support_text", fallback="Report an issue on GitLab").strip()
    notice_file = cfg.get("general", "notice_file", fallback="").strip()
    notice_style = cfg.get("general", "notice_style", fallback="warning").strip()

    bg_color = cfg.get("theme", "background_color", fallback="#0f172a")
    surface_color = cfg.get("theme", "surface_color", fallback="#1e293b")
    text_color = cfg.get("theme", "text_color", fallback="#f8fafc")
    border_color = cfg.get("theme", "border_color", fallback="#334155")

    logo_html = f'<img src="{logo_url}" alt="Logo" class="brand-logo">' if logo_url else ''

    support_html = ""
    if support_url:
        support_html = f"""
        <div class="support-card">
            <span>Experiencing an unlisted problem?</span>
            <a href="{support_url}" target="_blank" rel="noopener noreferrer" class="support-button">
                {support_text} &rarr;
            </a>
        </div>
        """

    max_severity = 0
    for s in services:
        sev = map_service_status(int(s.get("status", -1)))["severity"]
        if sev > max_severity:
            max_severity = sev

    if max_severity == 0:
        system_banner_class = "banner-ok"
        system_banner_title = "All Systems Operational"
        system_banner_desc = "All monitored services are functioning within normal parameters."
    elif max_severity in (1, 2):
        system_banner_class = "banner-warn"
        system_banner_title = "Active Incident / Degraded Performance"
        system_banner_desc = "Some components are currently experiencing degraded performance."
    else:
        system_banner_class = "banner-crit"
        system_banner_title = "Major System Outage"
        system_banner_desc = "One or more critical services are currently unavailable."

    log(f"Global system status determined: '{system_banner_title}'")

    notice_html = render_notice_card(notice_file, notice_style)
    root_services = build_service_tree(services)
    cards_html = [render_service_node(r, 0, expand_mode) for r in root_services]
    service_rows = "\n".join(cards_html) if cards_html else '<div class="service-card">No services found.</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="{refresh_sec}">
    <title>{title}</title>
    <style>
        :root {{
            --bg: {bg_color};
            --surface: {surface_color};
            --surface-hover: color-mix(in srgb, {surface_color} 88%, white 12%);
            --border: {border_color};
            --text-main: {text_color};
            --text-muted: color-mix(in srgb, {text_color} 60%, transparent);
            --color-ok: #10b981;
            --color-warn: #f59e0b;
            --color-info: #3b82f6;
            --color-crit: #ef4444;
        }}
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        body {{
            background-color: var(--bg);
            color: var(--text-main);
            min-height: 100vh;
            padding: 3rem 1.5rem;
            display: flex;
            justify-content: center;
        }}
        .container {{
            width: 100%;
            max-width: 760px;
        }}
        header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 2rem;
        }}
        .brand-container {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .brand-logo {{
            height: 36px;
            width: auto;
            max-width: 140px;
            object-fit: contain;
        }}
        .brand-text {{
            font-size: 1.5rem;
            font-weight: 700;
            letter-spacing: -0.025em;
        }}
        .live-tag {{
            font-size: 0.75rem;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 6px;
            background: var(--surface);
            padding: 4px 10px;
            border-radius: 9999px;
            border: 1px solid var(--border);
        }}
        .banner {{
            padding: 1.5rem;
            border-radius: 12px;
            margin-bottom: 1.75rem;
            border: 1px solid transparent;
        }}
        .banner-ok {{
            background: rgba(16, 185, 129, 0.1);
            border-color: rgba(16, 185, 129, 0.3);
            color: #34d399;
        }}
        .banner-warn {{
            background: rgba(245, 158, 11, 0.1);
            border-color: rgba(245, 158, 11, 0.3);
            color: #fbbf24;
        }}
        .banner-crit {{
            background: rgba(239, 68, 68, 0.1);
            border-color: rgba(239, 68, 68, 0.3);
            color: #f87171;
        }}
        .banner-title {{
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 0.25rem;
        }}
        .banner-sub {{
            font-size: 0.875rem;
            color: var(--text-muted);
        }}
        .notice-card {{
            margin-bottom: 2rem;
            border-radius: 12px;
            padding: 1.25rem 1.5rem;
            border: 1px solid transparent;
        }}
        .notice-warning {{
            background: rgba(245, 158, 11, 0.08);
            border-color: rgba(245, 158, 11, 0.35);
        }}
        .notice-info {{
            background: rgba(59, 130, 246, 0.08);
            border-color: rgba(59, 130, 246, 0.35);
        }}
        .notice-critical {{
            background: rgba(239, 68, 68, 0.08);
            border-color: rgba(239, 68, 68, 0.35);
        }}
        .notice-header {{
            margin-bottom: 0.85rem;
        }}
        .notice-body h2, .notice-body h3, .notice-body h4 {{
            margin-top: 0.5rem;
            margin-bottom: 0.5rem;
            font-weight: 600;
        }}
        .notice-body h2 {{ font-size: 1.2rem; }}
        .notice-body h3 {{ font-size: 1.1rem; }}
        .notice-body h4 {{ font-size: 1.0rem; }}
        .notice-body p {{
            margin-bottom: 0.65rem;
            font-size: 0.9rem;
            line-height: 1.55;
            color: var(--text-main);
        }}
        .notice-body p:last-child {{
            margin-bottom: 0;
        }}
        .notice-body ul {{
            margin-left: 1.25rem;
            margin-bottom: 0.75rem;
            font-size: 0.9rem;
            line-height: 1.55;
        }}
        .notice-body li {{
            margin-bottom: 0.25rem;
        }}
        .notice-body code {{
            background: rgba(255, 255, 255, 0.1);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.825rem;
            font-family: monospace;
        }}
        .notice-body a {{
            color: #60a5fa;
            text-decoration: underline;
            text-underline-offset: 2px;
        }}
        .notice-body a:hover {{
            color: #93c5fd;
        }}
        .notice-body blockquote {{
            border-left: 3px solid var(--border);
            padding-left: 0.85rem;
            font-style: italic;
            color: var(--text-muted);
            margin: 0.65rem 0;
            font-size: 0.875rem;
        }}
        .section-title {{
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 0.75rem;
            font-weight: 600;
        }}
        .services-list {{
            background: var(--surface);
            border-radius: 12px;
            border: 1px solid var(--border);
            overflow: hidden;
        }}
        .services-list > .service-card,
        .services-list > .service-group {{
            border-bottom: 1px solid var(--border);
        }}
        .services-list > .service-card:last-child,
        .services-list > .service-group:last-child {{
            border-bottom: none;
        }}
        .service-card {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 1.15rem 1.5rem;
            transition: background 0.15s ease;
        }}
        .service-card:hover {{
            background: var(--surface-hover);
        }}
        summary.service-card {{
            cursor: pointer;
            list-style: none;
            user-select: none;
        }}
        summary::-webkit-details-marker {{
            display: none;
        }}
        .service-left {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .chevron {{
            width: 18px;
            height: 18px;
            transition: transform 0.2s ease;
            color: var(--text-muted);
            flex-shrink: 0;
        }}
        details[open] > summary .chevron {{
            transform: rotate(90deg);
        }}
        .service-name {{
            font-weight: 600;
            font-size: 0.975rem;
        }}
        .service-desc {{
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 2px;
        }}
        .service-children {{
            background: color-mix(in srgb, var(--surface) 93%, black 7%);
            border-top: 1px solid var(--border);
            border-left: 2px solid color-mix(in srgb, var(--border) 80%, white 20%);
            margin-left: 1.75rem;
        }}
        .service-children .service-card {{
            padding: 0.85rem 1.25rem;
            border-bottom: 1px solid color-mix(in srgb, var(--border) 40%, transparent);
        }}
        .service-children .service-card:last-child {{
            border-bottom: none;
        }}
        .service-children .service-name {{
            font-size: 0.875rem;
            font-weight: 500;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 0.775rem;
            font-weight: 600;
        }}
        .service-children .badge {{
            font-size: 0.725rem;
            padding: 3px 8px;
        }}
        .badge-ok {{ background: rgba(16, 185, 129, 0.15); color: var(--color-ok); }}
        .badge-warn {{ background: rgba(245, 158, 11, 0.15); color: var(--color-warn); }}
        .badge-info {{ background: rgba(59, 130, 246, 0.15); color: var(--color-info); }}
        .badge-avg {{ background: rgba(249, 115, 22, 0.15); color: #fb923c; }}
        .badge-crit {{ background: rgba(239, 68, 68, 0.15); color: var(--color-crit); }}
        .dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }}
        .dot-ok {{ background: var(--color-ok); box-shadow: 0 0 8px rgba(16, 185, 129, 0.6); }}
        .dot-warn {{ background: var(--color-warn); box-shadow: 0 0 8px rgba(245, 158, 11, 0.6); }}
        .dot-info {{ background: var(--color-info); box-shadow: 0 0 8px rgba(59, 130, 246, 0.6); }}
        .dot-avg {{ background: #fb923c; box-shadow: 0 0 8px rgba(249, 115, 22, 0.6); }}
        .dot-crit {{ background: var(--color-crit); box-shadow: 0 0 8px rgba(239, 68, 68, 0.6); }}
        .support-card {{
            margin-top: 1.5rem;
            padding: 1.15rem 1.5rem;
            background: var(--surface);
            border-radius: 12px;
            border: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 0.9rem;
            color: var(--text-muted);
            gap: 1rem;
            flex-wrap: wrap;
        }}
        .support-button {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: color-mix(in srgb, var(--surface) 65%, white 35%);
            color: var(--text-main);
            text-decoration: none;
            font-size: 0.825rem;
            font-weight: 600;
            padding: 8px 16px;
            border-radius: 8px;
            border: 1px solid var(--border);
            transition: all 0.15s ease;
        }}
        .support-button:hover {{
            background: color-mix(in srgb, var(--surface) 40%, white 60%);
            border-color: color-mix(in srgb, var(--border) 60%, white 40%);
        }}
        footer {{
            margin-top: 2rem;
            text-align: center;
            font-size: 0.75rem;
            color: var(--text-muted);
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand-container">
                {logo_html}
                <div class="brand-text">{title}</div>
            </div>
            <div class="live-tag">
                <span class="dot dot-ok"></span> Live Monitoring
            </div>
        </header>

        <div class="banner {system_banner_class}">
            <div class="banner-title">{system_banner_title}</div>
            <div class="banner-sub">{system_banner_desc}</div>
        </div>

        {notice_html}

        <div class="section-title">Current Services</div>
        <div class="services-list">
            {service_rows}
        </div>

        {support_html}

        <footer>
            Last synchronized: {now_utc} &bull; Auto-refresh every {refresh_sec}s
        </footer>
    </div>
</body>
</html>
"""


def main():
    global VERBOSE
    args = parse_args()
    VERBOSE = args.verbose

    log(f"Loading configuration from '{args.config}'...")
    cfg = load_config(args.config)

    url = cfg.get("zabbix", "url")
    token = cfg.get("zabbix", "token")
    verify_ssl = cfg.getboolean("zabbix", "verify_ssl", fallback=True)
    out_path = cfg.get("general", "output_path", fallback="/var/www/html/index.html")

    services = fetch_zabbix_services(url, token, verify_ssl)

    log("Rendering status page HTML...")
    html_output = render_html(services, cfg)

    dest_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(dest_dir, exist_ok=True)

    log(f"Writing output file to '{out_path}'...")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_output)

    log(f"Status page successfully generated at '{out_path}' ({len(services)} services processed).")


if __name__ == "__main__":
    main()
