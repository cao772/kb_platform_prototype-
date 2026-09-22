"""Tune observed site links and collect a bounded, product-relevant sample."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from app.store import KnowledgeStore
from app.source_registry import SourceRegistryService
from app.site_extraction import SiteExtractionService
from scripts.live_first50 import AuditedFetcher

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/live_validation_stage37'


def main():
    store = KnowledgeStore(ROOT / 'data/knowledge.db')
    registry = SourceRegistryService(store)
    service = SiteExtractionService(store, registry, ROOT / 'data/source_downloads')
    doe = service.plan('US-DOE-EFFICIENCY')['config']
    doe['start_urls'] = ['https://www.energy.gov/cmei/buildings/standards-and-test-procedures']
    doe['discovery']['include_url_patterns'] = [r'/cmei/buildings/(consumer-clothes-washers|consumer-dishwashers|refrigeration-products|consumer-room-air-conditioners|television-sets)$']
    doe['discovery']['detail_text_keywords'] = []
    doe['pagination']['next_text_keywords'] = []
    doe['pagination']['url_patterns'] = []
    doe['limits'].update(max_pages=8, max_details=5, max_attachments=2)
    service.update_plan('US-DOE-EFFICIENCY', config=doe)
    de = service.plan('DE-LAW')['config']
    de['start_urls'] = ['https://www.gesetze-im-internet.de/prodsg_2021/']
    de['discovery']['include_url_patterns'] = [r'/prodsg_2021/.*\.html$']
    de['discovery']['detail_text_keywords'] = []
    de['pagination']['next_text_keywords'] = []
    de['pagination']['url_patterns'] = []
    de['limits'].update(max_pages=8, max_details=4, max_attachments=3)
    service.update_plan('DE-LAW', config=de)

    def run(key):
        fetcher = AuditedFetcher()
        crawler = SiteExtractionService(store, registry, ROOT / 'data/source_downloads', fetcher=fetcher)
        result = crawler.run(key, max_pages=8, auto_ingest=False)
        result['requests'] = fetcher.requests
        (OUT / (key + '-targeted.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, ['DE-LAW', 'US-DOE-EFFICIENCY']))


if __name__ == '__main__':
    main()
