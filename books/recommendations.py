"""도서 상세 페이지에서 사용할 추천 로직."""

import hashlib
import logging
import math
import os
import re

from django.core.cache import cache

from .models import Book


logger = logging.getLogger(__name__)
EMBEDDING_MODEL = "text-embedding-3-small"
MAX_EMBEDDING_CANDIDATES = 40
CACHE_SECONDS = 60 * 60 * 24


def _fallback_recommendations(book, limit):
    """AI API를 사용할 수 없을 때 같은 장르의 책을 추천한다."""
    books = Book.objects.filter(category_id=book.category_id).exclude(pk=book.pk)
    return list(books.order_by("-customer_review_rank", "title")[:limit])


def _ordered_books(book_ids):
    """캐시에 저장된 ID 순서를 유지해 도서 객체를 가져온다."""
    books_by_id = Book.objects.in_bulk(book_ids)
    return [books_by_id[book_id] for book_id in book_ids if book_id in books_by_id]


def _tokens(text):
    return set(re.findall(r"[0-9A-Za-z가-힣]+", text.lower()))


def _cosine_similarity(left, right):
    dot_product = sum(a * b for a, b in zip(left, right))
    left_length = math.sqrt(sum(value * value for value in left))
    right_length = math.sqrt(sum(value * value for value in right))
    if not left_length or not right_length:
        return 0
    return dot_product / (left_length * right_length)


def _embedding_text(book):
    # API 입력을 짧게 유지해 요청 토큰과 비용을 줄인다.
    return "\n".join((book.title, book.author, book.category.name, book.description[:1200]))


def _candidate_books(book):
    """장르와 단어가 가까운 책만 뽑아 임베딩 요청량을 제한한다."""
    target_words = _tokens(f"{book.title} {book.author} {book.description[:1200]}")
    candidates = Book.objects.exclude(pk=book.pk).select_related("category").only(
        "id", "title", "author", "description", "category_id", "category__name"
    )

    ranked = []
    for candidate in candidates:
        candidate_words = _tokens(f"{candidate.title} {candidate.author} {candidate.description[:1200]}")
        overlap = len(target_words & candidate_words) / max(len(target_words), 1)
        same_category = candidate.category_id == book.category_id
        # 장르를 먼저 고려하고 제목·소개에 겹치는 단어가 있으면 후보 순위를 높인다.
        prefilter_score = (2 if same_category else 0) + overlap
        ranked.append((prefilter_score, candidate))

    ranked.sort(key=lambda item: (-item[0], item[1].pk))
    return [candidate for _, candidate in ranked[:MAX_EMBEDDING_CANDIDATES]]


def get_recommendations(book, limit=3):
    """(추천 도서 목록, 추천 방식)을 반환한다.

    추천 방식은 ``ai`` 또는 ``category``이며, AI 요청이 실패해도 상세 페이지는 계속 열린다.
    """
    limit = max(0, min(limit, 3))
    api_key = os.getenv("GMS_KEY")
    base_url = os.getenv("GMS_URL")
    if not api_key or not base_url:
        return _fallback_recommendations(book, limit), "category"

    text_signature = hashlib.sha256(
        f"{book.pk}:{book.category_id}:{book.title}:{book.author}:{book.description}".encode("utf-8")
    ).hexdigest()[:20]
    cache_key = f"book-recommendations-v1-{limit}-{text_signature}"
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        mode, book_ids = cached_result
        return _ordered_books(book_ids)[:limit], mode

    candidates = _candidate_books(book)
    if not candidates:
        return [], "ai"

    try:
        # 임베딩 호출은 한 번에 묶고, 재시도는 꺼서 불필요한 API 사용을 막는다.
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=10)
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=[_embedding_text(book), *[_embedding_text(candidate) for candidate in candidates]],
        )
        vectors = sorted(response.data, key=lambda item: item.index)
        if len(vectors) != len(candidates) + 1:
            raise ValueError("임베딩 응답 개수가 요청과 일치하지 않습니다.")

        target_vector = vectors[0].embedding
        scored_books = [
            (_cosine_similarity(target_vector, vectors[index + 1].embedding), candidate.pk)
            for index, candidate in enumerate(candidates)
        ]
        scored_books.sort(key=lambda item: (-item[0], item[1]))
        book_ids = [book_id for _, book_id in scored_books[:limit]]
        cache.set(cache_key, ("ai", book_ids), CACHE_SECONDS)
        return _ordered_books(book_ids), "ai"
    except Exception:
        # 키나 서비스 문제를 페이지 오류로 번지게 하지 않고 대체 추천을 기억한다.
        logger.warning("GMS 임베딩 추천에 실패해 카테고리 기반 추천으로 전환합니다.")
        fallback_books = _fallback_recommendations(book, limit)
        cache.set(cache_key, ("category", [item.pk for item in fallback_books]), 300)
        return fallback_books, "category"
