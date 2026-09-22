"""Bounded real-network deep crawl; keep a per-request audit alongside DB runs."""
from __future__ import annotations

import argparse
import csv
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from app.site_extraction import SiteExtractionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore

ROOT = Path(__file__).resolve().parents[1]
HOST_LOCKS = {}
HOST_GUARD = threading.Lock()


class AuditedFetcher:
    def __init__(self):
        self.requests = []
        self.robots = {}
        self.started = time.monotonic()

    def __call__(self, url, headers, timeout, max_bytes):
        if len(self.requests) >= 12 or time.monotonic() - self.started > 150:
            raise ValueError("本轮每站12次请求/150秒预算已用完，待续采")
        origin = '{0.scheme}://{0.netloc}'.format(urlparse(url))
        with HOST_GUARD:
            lock = HOST_LOCKS.setdefault(origin, threading.Lock())
        record = {'url': url, 'http_status': None, 'robots': 'unknown'}
        self.requests.append(record)
        with lock:
            time.sleep(1)
            try:
                if origin not in self.robots:
                    rp = None
                    try:
                        with urlopen(Request(origin + '/robots.txt', headers=headers), timeout=6) as response:
                            body = response.read(256 * 1024).decode('utf-8', errors='replace')
                        rp = RobotFileParser()
                        rp.parse(body.splitlines())
                    except HTTPError as exc:
                        if exc.code in (401, 403):
                            rp = RobotFileParser()
                            rp.parse(['User-agent: *', 'Disallow: /'])
                    except Exception:
                        pass
                    self.robots[origin] = rp
                rp = self.robots[origin]
                if rp is not None:
                    allowed = rp.can_fetch(headers['User-Agent'], url)
                    record['robots'] = 'allowed' if allowed else 'disallowed'
                    if not allowed:
                        raise ValueError('robots.txt disallows this path')
                    delay = rp.crawl_delay(headers['User-Agent']) or 0
                    if delay > 10:
                        raise ValueError('robots crawl-delay exceeds this bounded run; needs scheduled crawl')
                    if delay > 1:
                        time.sleep(delay - 1)
                with urlopen(Request(url, headers=headers), timeout=min(timeout, 10)) as response:
                    record.update(http_status=response.status, final_url=response.geturl(),
                                  content_type=response.headers.get('Content-Type', ''))
                    body = response.read(min(max_bytes, 12 * 1024 * 1024) + 1)
                if len(body) > min(max_bytes, 12 * 1024 * 1024):
                    raise ValueError('response exceeds size limit')
                sample = body[:30000].lower()
                if any(s in sample for s in (b'<title>just a moment', b'<title>access denied', b'<title>request rejected', b'awswaf-captcha', b'<title>federal register :: request access')):
                    raise ValueError('access challenge returned instead of source content')
                if not body:
                    raise ValueError('empty response')
                record['bytes'] = len(body)
                return body, record['http_status'], record['content_type']
            except Exception as exc:
                if isinstance(exc, HTTPError):
                    record['http_status'] = exc.code
                record['error'] = str(exc)
                raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--workers', type=int, default=5)
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    store = KnowledgeStore(ROOT / 'data/knowledge.db')
    registry = SourceRegistryService(store)
    SiteExtractionService(store, registry, ROOT / 'data/source_downloads')
    keys = [item['source_key'] for item in json.loads((ROOT / 'data/source_collection_first50.json').read_text())]
    services = {key: SiteExtractionService(store, registry, ROOT / 'data/source_downloads', fetcher=AuditedFetcher()) for key in keys}

    def run(key):
        service = services[key]
        service.fetcher.started = time.monotonic()
        result = service.run(key, max_pages=6, max_items=120, auto_ingest=False)
        result['requests'] = service.fetcher.requests
        (output / (key + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({k: result[k] for k in ('source_key', 'id', 'status', 'pages_fetched', 'items_discovered', 'attachments_discovered')}, ensure_ascii=False), flush=True)
        return result

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(run, key): key for key in keys}
        for future in as_completed(pending):
            try:
                results.append(future.result())
            except Exception as exc:
                result = {'source_key': pending[future], 'status': 'runner_failed', 'error': str(exc)}
                results.append(result)
                print(json.dumps(result), flush=True)
    results.sort(key=lambda r: keys.index(r['source_key']))
    (output / 'crawl_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    columns = ['source_key', 'id', 'status', 'pages_fetched', 'items_discovered', 'attachments_discovered', 'error']
    with (output / 'crawl_results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    print('Finished: ' + str(output), flush=True)


if __name__ == '__main__':
    main()
