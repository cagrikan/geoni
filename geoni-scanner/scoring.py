"""
AI Visibility Score calculation engine.
Computes 0-100 score based on multiple factors.
"""

import logging
from typing import Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScoreBreakdown:
    """Detailed score breakdown."""
    index_coverage: float  # 30% weight
    authority_score: float  # 25% weight
    freshness_score: float  # 20% weight
    schema_score: float  # 15% weight
    engagement_score: float  # 10% weight


class VisibilityScorer:
    """Calculates AI Visibility Score."""
    
    # Weights for each component
    WEIGHTS = {
        "index_coverage": 0.30,
        "authority": 0.25,
        "freshness": 0.20,
        "schema": 0.15,
        "engagement": 0.10
    }
    
    def __init__(self, crawl_result: Dict, indexing_status: Dict):
        self.crawl_result = crawl_result
        self.indexing_status = indexing_status
    
    def calculate(self) -> Dict:
        """Calculate complete visibility score."""
        
        # Component scores
        index_coverage = self._calculate_index_coverage()
        authority = self._calculate_authority()
        freshness = self._calculate_freshness()
        schema = self._calculate_schema_markup()
        engagement = self._calculate_engagement()
        
        # Weighted sum
        overall_score = (
            (index_coverage * self.WEIGHTS["index_coverage"]) +
            (authority * self.WEIGHTS["authority"]) +
            (freshness * self.WEIGHTS["freshness"]) +
            (schema * self.WEIGHTS["schema"]) +
            (engagement * self.WEIGHTS["engagement"])
        )
        
        return {
            "overall_score": min(100, max(0, int(overall_score))),
            "breakdown": {
                "index_coverage": round(index_coverage, 2),
                "authority": round(authority, 2),
                "freshness": round(freshness, 2),
                "schema": round(schema, 2),
                "engagement": round(engagement, 2)
            },
            "platform_scores": self._calculate_platform_scores()
        }
    
    def _calculate_index_coverage(self) -> float:
        """
        Calculate index coverage score (0-100).
        Based on percentage of pages indexed across all platforms.
        """
        total_pages = self.crawl_result.get("total_pages", 0)
        if total_pages == 0:
            return 0
        
        indexed_count = self.indexing_status.get("indexed_count", 0)
        coverage = (indexed_count / total_pages) * 100
        
        # Scale to 0-100 with curve
        # 100% coverage = 100 points
        # 50% coverage = 75 points
        # 0% coverage = 0 points
        if coverage >= 80:
            return 100
        elif coverage >= 50:
            return 50 + (coverage - 50) * 1.0
        else:
            return coverage * (50 / 50)
    
    def _calculate_authority(self) -> float:
        """
        Calculate authority score (0-100).
        Factors:
        - Domain age (newer = lower, older = higher)
        - External links (more = higher)
        - Industry relevance
        - Backlink quality
        """
        # TODO: Integrate with Moz API, Ahrefs, or similar
        # For MVP: return baseline score
        return 65.0
    
    def _calculate_freshness(self) -> float:
        """
        Calculate content freshness score (0-100).
        Factors:
        - Last modified dates
        - Regular update cadence
        - New page additions
        """
        pages = self.crawl_result.get("pages", [])
        if not pages:
            return 0
        
        # Count pages with schema.org date info or recent modifications
        # For MVP: assume medium freshness
        return 72.0
    
    def _calculate_schema_markup(self) -> float:
        """
        Calculate schema.org markup completeness (0-100).
        Factors:
        - Presence of structured data
        - Schema.org types used (Organization, Article, Product, etc.)
        - Completeness of schema fields
        """
        pages = self.crawl_result.get("pages", [])
        if not pages:
            return 0
        
        pages_with_schema = sum(
            1 for p in pages if p.get("has_schema_markup", False)
        )
        
        schema_score = (pages_with_schema / len(pages)) * 100
        return schema_score
    
    def _calculate_engagement(self) -> float:
        """
        Calculate engagement score (0-100).
        Factors:
        - Social mentions
        - Citation frequency
        - Backlink diversity
        - Brand searches
        """
        # TODO: Integrate with social listening APIs
        # For MVP: return baseline score
        return 58.0
    
    def _calculate_platform_scores(self) -> Dict[str, int]:
        """
        Calculate platform-specific visibility scores.
        """
        platform_mapping = {
            "openai": "chatgpt",
            "perplexity": "perplexity",
            "google": "google_ai",
            "anthropic": "claude",
            "bing": "bing"
        }
        
        platform_scores = {}
        total_pages = self.crawl_result.get("total_pages", 0)
        
        for api_name, display_name in platform_mapping.items():
            indexed = self.indexing_status.get(api_name, 0)
            if total_pages > 0:
                coverage = (indexed / total_pages) * 100
                # Scale coverage to 0-100 score
                score = min(100, int(coverage * 1.2))  # Slight boost for visible pages
                platform_scores[display_name] = {
                    "score": score,
                    "indexed_pages": indexed
                }
        
        return platform_scores


def compute_visibility_score(crawl_result: Dict, indexing_status: Dict) -> Dict:
    """
    Compute AI Visibility Score.
    
    Args:
        crawl_result: Output from crawler
        indexing_status: Output from indexing check
    
    Returns:
        Dict with overall_score, breakdown, and platform_scores
    """
    try:
        scorer = VisibilityScorer(crawl_result, indexing_status)
        result = scorer.calculate()
        logger.info(f"Calculated visibility score: {result['overall_score']}")
        return result
    except Exception as e:
        logger.error(f"Error calculating visibility score: {e}")
        return {
            "overall_score": 0,
            "breakdown": {
                "index_coverage": 0,
                "authority": 0,
                "freshness": 0,
                "schema": 0,
                "engagement": 0
            },
            "platform_scores": {}
        }
