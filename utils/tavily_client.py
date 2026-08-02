import logging
from tavily import TavilyClient
from config import TAVILY_API_KEY

logger = logging.getLogger(__name__)
client = TavilyClient(api_key=TAVILY_API_KEY)

async def search_question(question: str) -> str:
    """Search web for question context. Returns empty string if search fails."""
    try:
        response = client.search(
            query=question + " SSC UPSC answer",
            max_results=3,
            search_depth="basic"
        )
        results = []
        for r in response.get("results", []):
            results.append(f"Source: {r.get('title','')}\n{r.get('content','')[:300]}")
        combined = "\n\n".join(results)
        logger.info("Tavily returned %d results for: %s", len(results), question[:60])
        return combined
    except Exception:
        logger.error("Tavily search failed for: %s", question[:60], exc_info=True)
        return ""
