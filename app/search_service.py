from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from .config import AppConfig

logger = logging.getLogger(__name__)


class SearchService:
    SEARCH_ENDPOINT = "https://voila.ca/api/v6/products/search"
    CART_ENDPOINT = "https://voila.ca/api/cart/v1/carts/active"
    PRODUCT_URL = "https://voila.ca/products/{code}"

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------
    # Session helpers

    def parse_terms(self, raw_terms: str) -> Tuple[List[str], List[str], int, bool]:
        parts = re.split(r"[,\s]+", raw_terms.strip())
        terms: List[str] = []
        duplicates: List[str] = []
        seen = set()
        contains_ea = False

        for part in filter(None, parts):
            normalized = part.strip()
            if not normalized:
                continue
            if normalized.upper().endswith("EA"):
                contains_ea = True
            if normalized not in seen:
                seen.add(normalized)
                terms.append(normalized)
            else:
                duplicates.append(normalized)

        return terms, duplicates, len(duplicates), contains_ea

    def get_region_info(self, global_sid: str) -> Dict[str, Optional[str]]:
        headers = {
            "accept": "application/json; charset=utf-8",
            "client-route-id": str(uuid.uuid4()),
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        cookies = {"global_sid": global_sid}

        try:
            response = requests.get(
                self.CART_ENDPOINT,
                headers=headers,
                cookies=cookies,
                timeout=self.cfg.request_timeout,
            )
        except requests.Timeout:
            logger.warning("Region lookup timed out")
            return self._region_error("Timeout", "API request timed out")
        except Exception as exc:  # pragma: no cover
            logger.warning("Region lookup failed: %s", exc)
            return self._region_error("Error", str(exc)[:80])

        if response.status_code != 200:
            logger.warning("Region lookup returned status %s", response.status_code)
            return self._fallback_region(response.text)

        try:
            payload = response.json()
        except ValueError:
            payload = None

        region = self._extract_region(payload)
        if region.get("regionId"):
            return region
        return self._fallback_region(response.text)

    def _extract_region(self, payload: Optional[Dict]) -> Dict[str, Optional[str]]:
        default = {
            "regionId": None,
            "nickname": None,
            "displayAddress": None,
            "postalCode": None,
        }
        if not isinstance(payload, dict):
            return default

        region_id = payload.get("regionId")
        nickname = None
        display = None
        postal = None

        delivery = (
            payload.get("defaultCheckoutGroup", {})
            .get("delivery", {})
            .get("addressDetails", {})
        )
        if isinstance(delivery, dict):
            nickname = delivery.get("nickname")
            display = delivery.get("displayAddress")
            postal = delivery.get("postalCode")

        if region_id and not nickname:
            nickname = f"Region {region_id}"

        return {
            "regionId": region_id,
            "nickname": nickname,
            "displayAddress": display,
            "postalCode": postal,
        }

    def _fallback_region(self, body: str) -> Dict[str, Optional[str]]:
        def first(regex: str) -> Optional[str]:
            match = re.search(regex, body)
            return match.group(1) if match else None

        region_id = first(r'"regionId"\s*:\s*"?([0-9a-fA-F-]+)"?')
        nickname = first(r'"nickname"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"')
        display = first(r'"displayAddress"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"')
        postal = first(r'"postalCode"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"')

        if region_id and not nickname:
            nickname = f"Region {region_id}"

        if region_id:
            return {
                "regionId": region_id,
                "nickname": nickname,
                "displayAddress": display,
                "postalCode": postal,
            }

        return self._region_error("Unknown", "Could not determine region")

    @staticmethod
    def _region_error(nickname: str, message: str) -> Dict[str, Optional[str]]:
        return {
            "regionId": None,
            "nickname": nickname,
            "displayAddress": message,
            "postalCode": None,
        }

    # ------------------------------------------------------------------
    # Search processing

    def process_chunk(
        self,
        terms: Sequence[str],
        *,
        global_sid: str,
        limit: str | int,
        search_type: str,
        region_id: Optional[str],
    ) -> Tuple[List[Dict], int, int]:
        if not terms:
            return [], 0, 0

        max_workers = max(1, min(self.cfg.max_workers, len(terms)))
        results: List[Dict] = []
        total_found = 0
        processed = 0
        is_article_search = search_type.lower() == "article"

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._process_term,
                    term,
                    global_sid,
                    limit,
                    is_article_search,
                    region_id,
                ): term
                for term in terms
            }

            for future in as_completed(futures):
                processed += 1
                term = futures[future]
                try:
                    product_list, term_count = future.result()
                    total_found += term_count
                    results.extend(product_list)
                except Exception as exc:  # pragma: no cover
                    logger.exception("Error processing term '%s': %s", term, exc)
                    results.append(self._not_found(term, "Error processing the article. Please try again."))

        return results, total_found, processed

    def _process_term(
        self,
        term: str,
        global_sid: str,
        limit: str | int,
        is_article_search: bool,
        region_id: Optional[str],
    ) -> Tuple[List[Dict], int]:
        payload = self._fetch_product_data(term, global_sid, region_id=region_id)
        if not payload:
            return [self._not_found(term)], 0

        entities = payload.get("entities", {}).get("product", {})
        if not isinstance(entities, dict) or not entities:
            return [self._not_found(term)], 0

        total_found = len(entities)

        if is_article_search:
            product_ids = list(entities.keys())[:1]
        else:
            max_items = self._resolve_limit(limit)
            product_ids = list(entities.keys())[:max_items]

        products = [
            self._extract_product_info(entities[product_id], term)
            for product_id in product_ids
            if isinstance(entities.get(product_id), dict)
        ]

        return products or [self._not_found(term)], total_found

    def _resolve_limit(self, limit: str | int) -> int:
        if isinstance(limit, int):
            return max(1, min(limit, 50))
        if isinstance(limit, str):
            if limit.lower() == "all":
                return 50
            try:
                return max(1, min(int(limit), 50))
            except ValueError:
                return 10
        return 10

    def _fetch_product_data(
        self,
        term: str,
        global_sid: str,
        *,
        region_id: Optional[str],
    ) -> Optional[Dict]:
        headers = {
            "accept": "application/json; charset=utf-8",
            "client-route-id": str(uuid.uuid4()),
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        }
        cookies = {"global_sid": global_sid}
        params = {"term": term}
        if region_id:
            params["regionId"] = region_id

        try:
            response = requests.get(
                self.SEARCH_ENDPOINT,
                headers=headers,
                params=params,
                cookies=cookies,
                timeout=self.cfg.request_timeout,
            )
        except requests.Timeout:
            logger.warning("Search request timed out for term '%s'", term)
            return None
        except Exception as exc:  # pragma: no cover
            logger.warning("Search request failed for term '%s': %s", term, exc)
            return None

        if response.status_code != 200:
            logger.debug(
                "Search API returned %s for '%s'; attempting fallback page lookup",
                response.status_code,
                term,
            )
            return self._product_payload_from_page(
                term, global_sid, region_id=region_id
            )

        text = response.text
        if '"productId"' not in text and '"retailerProductId"' not in text:
            return {"entities": {"product": {}}}

        try:
            return response.json()
        except ValueError:
            return self._fallback_product_payload(text)

    def _product_payload_from_page(
        self,
        term: str,
        global_sid: str,
        *,
        region_id: Optional[str],
    ) -> Optional[Dict]:
        if not re.match(r"^[A-Za-z0-9_-]{3,}$", term):
            return None

        headers = {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/141.0.0.0 Safari/537.36"
            ),
            "Referer": "https://voila.ca/",
        }
        cookies = {"global_sid": global_sid} if global_sid else {}
        params = {"regionId": region_id} if region_id else {}

        try:
            response = requests.get(
                self.PRODUCT_URL.format(code=term),
                headers=headers,
                params=params,
                cookies=cookies,
                timeout=self.cfg.request_timeout,
            )
        except requests.Timeout:
            logger.warning("Fallback page lookup timed out for '%s'", term)
            return None
        except Exception as exc:
            logger.warning("Fallback page lookup failed for '%s': %s", term, exc)
            return None

        if response.status_code != 200:
            logger.debug(
                "Fallback page lookup returned %s for '%s'",
                response.status_code,
                term,
            )
            return None

        match = re.search(r"window.__INITIAL_STATE__=(\{.*?\})</script>", response.text)
        if not match:
            return None

        try:
            state = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.debug("Failed to parse fallback JSON for '%s'", term)
            return None

        entities = state.get("entities", {}).get("product")
        if not isinstance(entities, dict):
            return None
        return {"entities": {"product": entities}}

    def _fallback_product_payload(self, text: str) -> Optional[Dict]:
        products: Dict[str, Dict] = {}
        for match in re.finditer(r"\{[^{}]*\"productId\"\s*:\s*\"([^\"]+)\"[^{}]*\}", text):
            snippet = match.group(0)
            try:
                obj = json.loads(snippet)
            except json.JSONDecodeError:
                continue
            product_id = obj.get("productId")
            if product_id:
                products[product_id] = obj

        if not products:
            return None
        return {"entities": {"product": products}}

    def _extract_product_info(self, product: Dict, term: str) -> Dict:
        current_price = None
        original_price = None
        unit_price = None
        unit_label = None

        price_info = product.get("price") or {}
        current_block = price_info.get("current") or {}
        original_block = price_info.get("original") or {}

        if isinstance(current_block, dict):
            current_price = current_block.get("amount")
        if isinstance(original_block, dict):
            original_price = original_block.get("amount")

        unit_info = price_info.get("unit") or {}
        unit_current = unit_info.get("current") or {}
        if isinstance(unit_current, dict):
            unit_price = unit_current.get("amount")
        unit_label = unit_info.get("label")

        discount_pct = None
        try:
            cp = float(current_price) if current_price is not None else None
            op = float(original_price) if original_price is not None else None
            if cp is not None and op and op > cp:
                discount_pct = round(((op - cp) / op) * 100)
        except (TypeError, ValueError):
            discount_pct = None

        offers = product.get("offers")
        if isinstance(offers, list):
            offers = offers[:5]
        else:
            offers = []

        return {
            "found": True,
            "searchTerm": term,
            "productId": product.get("productId"),
            "retailerProductId": product.get("retailerProductId"),
            "name": product.get("name"),
            "brand": product.get("brand"),
            "available": product.get("available"),
            "category": product.get("categoryPath"),
            "imageUrl": product.get("image", {}).get("baseUrl")
            if isinstance(product.get("image"), dict)
            else product.get("imageUrl"),
            "currentPrice": current_price,
            "originalPrice": original_price,
            "discountPercentage": discount_pct,
            "unitPrice": unit_price,
            "unitLabel": unit_label,
            "currency": product.get("currency", "CAD"),
            "offers": offers,
        }

    @staticmethod
    def _not_found(term: str, message: str | None = None) -> Dict:
        return {
            "found": False,
            "searchTerm": term,
            "productId": None,
            "retailerProductId": None,
            "name": f"Article Not Found: {term}",
            "brand": None,
            "available": False,
            "category": None,
            "imageUrl": None,
            "notFoundMessage": message
            or 'The article "{term}" was not found. It may not be published yet or could be a typo.'.format(
                term=term
            ),
        }
