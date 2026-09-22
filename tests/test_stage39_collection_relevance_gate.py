import tempfile
from pathlib import Path

from app.site_extraction import SiteExtractionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / 'stage39.db')
        service = SiteExtractionService(store, SourceRegistryService(store), root / 'downloads')
        config = service.plan('EU-EURLEX')['config']
        relevant = service._evaluate_relevance(
            url='https://example.test/legal-content/100', item_type='detail',
            title='Regulation 2026/100', text='Current product safety rule',
            fields={'code': ['Regulation 2026/100']}, config=config,
            parent_relevant=True, link_text='Regulation 2026/100',
        )
        assert relevant['status'] == 'relevant'
        noise = service._evaluate_relevance(
            url='https://example.test/help', item_type='detail',
            title='Accessibility', text='Accessibility help center', fields={},
            config=config, parent_relevant=True, link_text='help',
        )
        assert noise['status'] == 'irrelevant'
        review = service._evaluate_relevance(
            url='https://example.test/misc', item_type='detail',
            title='General information', text='x' * 200, fields={},
            config=config, parent_relevant=False, link_text='general',
        )
        assert review['status'] == 'needs_review'
    print('OK: stage39 business relevance classification passed')


if __name__ == '__main__':
    main()
