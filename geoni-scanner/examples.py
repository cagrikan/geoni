#!/usr/bin/env python3
"""
GEONI Visibility Scanner - Example API Usage

This script demonstrates how to use the API and test the audit pipeline.

Usage:
    python examples.py
    
Or with curl:
    curl -X POST http://localhost:8000/api/audit/quick \
      -H "Content-Type: application/json" \
      -d '{"domain": "example.com", "email": "test@example.com"}'
"""

import httpx
import asyncio
import time
import json
from typing import Dict, Optional

BASE_URL = "http://localhost:8000"
TIMEOUT = 30


class GeoniAPIClient:
    """Simple client for GEONI API."""
    
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=TIMEOUT)
    
    async def health_check(self) -> Dict:
        """Check API health."""
        response = await self.client.get(f"{self.base_url}/health")
        return response.json()
    
    async def start_audit(
        self,
        domain: str,
        email: str,
        competitors: Optional[list] = None,
        page_limit: int = 500
    ) -> Dict:
        """Start a new audit."""
        payload = {
            "domain": domain,
            "email": email,
            "competitors": competitors or [],
            "page_limit": page_limit
        }
        response = await self.client.post(
            f"{self.base_url}/api/audit/quick",
            json=payload
        )
        return response.json()
    
    async def get_audit_status(self, job_id: str) -> Dict:
        """Get audit status."""
        response = await self.client.get(
            f"{self.base_url}/api/audit/{job_id}"
        )
        return response.json()
    
    async def get_audit_results(self, job_id: str) -> Dict:
        """Get audit results (when complete)."""
        response = await self.client.get(
            f"{self.base_url}/api/audit/{job_id}/results"
        )
        return response.json()
    
    async def poll_until_complete(
        self,
        job_id: str,
        max_wait: int = 600,
        poll_interval: int = 5
    ) -> Dict:
        """Poll audit status until complete."""
        start_time = time.time()
        
        while True:
            status = await self.get_audit_status(job_id)
            print(f"Status: {status.get('status')}")
            
            if status.get("status") in ["complete", "failed"]:
                if status.get("status") == "complete":
                    # Fetch results
                    results = await self.get_audit_results(job_id)
                    return results
                else:
                    raise Exception(f"Audit failed: {status.get('error_message')}")
            
            elapsed = time.time() - start_time
            if elapsed > max_wait:
                raise TimeoutError(f"Audit did not complete within {max_wait}s")
            
            await asyncio.sleep(poll_interval)
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


async def example_basic_audit():
    """Example 1: Basic domain audit."""
    print("\n" + "="*60)
    print("Example 1: Basic Domain Audit")
    print("="*60)
    
    client = GeoniAPIClient()
    
    try:
        # Health check
        print("\n1. Checking API health...")
        health = await client.health_check()
        print(f"   ✅ API Status: {health.get('status')}")
        print(f"   Version: {health.get('version')}")
        
        # Start audit
        print("\n2. Starting audit for 'example.com'...")
        audit_response = await client.start_audit(
            domain="example.com",
            email="test@example.com",
            page_limit=50  # Small limit for testing
        )
        job_id = audit_response.get("job_id")
        print(f"   ✅ Audit started")
        print(f"   Job ID: {job_id}")
        print(f"   Status: {audit_response.get('status')}")
        
        # Poll until complete
        print("\n3. Waiting for audit to complete...")
        print("   (This may take a few minutes...)")
        results = await client.poll_until_complete(job_id)
        
        print("\n4. Audit Results:")
        print(f"   Overall Score: {results.get('overall_score')}/100")
        print(f"   Pages Crawled: {results.get('total_pages_crawled')}")
        print(f"   Pages Indexed: {results.get('total_pages_indexed')}")
        
        # Score breakdown
        breakdown = results.get("score_breakdown", {}).get("breakdown", {})
        print("\n   Score Breakdown:")
        for component, score in breakdown.items():
            print(f"      {component}: {score}")
        
        # Topics
        performing = results.get("top_performing_topics", [])
        print(f"\n   Top Performing Topics: {len(performing)}")
        for topic in performing[:3]:
            print(f"      - {topic.get('topic_name')} ({topic.get('mention_count')} mentions)")
        
        opportunities = results.get("opportunity_topics", [])
        print(f"\n   Opportunity Topics: {len(opportunities)}")
        for topic in opportunities[:3]:
            print(f"      - {topic.get('topic_name')}")
    
    finally:
        await client.close()


async def example_competitor_analysis():
    """Example 2: Competitor comparison."""
    print("\n" + "="*60)
    print("Example 2: Competitor Analysis")
    print("="*60)
    
    client = GeoniAPIClient()
    
    try:
        print("\n1. Starting audit with competitor analysis...")
        audit_response = await client.start_audit(
            domain="example.com",
            email="competitor@example.com",
            competitors=["competitor1.com", "competitor2.com"],
            page_limit=100
        )
        job_id = audit_response.get("job_id")
        print(f"   Job ID: {job_id}")
        
        print("\n2. Polling status...")
        results = await client.poll_until_complete(job_id)
        
        print(f"\n3. Score: {results.get('overall_score')}/100")
        
        # In future, this will include competitor comparison
        print("\n   (Competitor comparison features coming soon)")
    
    finally:
        await client.close()


async def example_manual_polling():
    """Example 3: Manual status polling."""
    print("\n" + "="*60)
    print("Example 3: Manual Status Polling")
    print("="*60)
    
    client = GeoniAPIClient()
    
    try:
        print("\n1. Starting audit...")
        audit_response = await client.start_audit(
            domain="example.com",
            email="manual@example.com",
            page_limit=25
        )
        job_id = audit_response.get("job_id")
        print(f"   Job ID: {job_id}")
        
        print("\n2. Manual polling (checking status every 5 seconds)...")
        for i in range(10):  # Max 50 seconds
            status = await client.get_audit_status(job_id)
            print(f"   Check {i+1}: {status.get('status')}")
            
            if status.get("status") == "complete":
                print("\n   ✅ Audit complete!")
                results = await client.get_audit_results(job_id)
                print(f"   Final Score: {results.get('overall_score')}/100")
                break
            elif status.get("status") == "failed":
                print(f"   ❌ Audit failed: {status.get('error_message')}")
                break
            
            await asyncio.sleep(5)
    
    finally:
        await client.close()


async def main():
    """Run examples."""
    print("""
    ╔════════════════════════════════════════════════════╗
    ║   GEONI Visibility Scanner - API Examples          ║
    ║   http://localhost:8000                            ║
    ╚════════════════════════════════════════════════════╝
    """)
    
    print("\n📋 Available examples:")
    print("   1. Basic domain audit")
    print("   2. Competitor analysis")
    print("   3. Manual status polling")
    
    # Run all examples
    await example_basic_audit()
    # await example_competitor_analysis()
    # await example_manual_polling()
    
    print("\n" + "="*60)
    print("Examples complete! ✅")
    print("="*60)


if __name__ == "__main__":
    # Make sure API is running
    print("Checking API connectivity...")
    try:
        response = httpx.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            print("✅ API is running!\n")
            asyncio.run(main())
        else:
            print(f"❌ API returned status {response.status_code}")
    except Exception as e:
        print(f"❌ Could not connect to API: {e}")
        print(f"\nMake sure the API is running:")
        print(f"  docker-compose up -d")
        print(f"  # OR")
        print(f"  uvicorn main:app --reload")
