from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from .models import Book, Category, Comment, Thread
from .recommendations import get_recommendations


User = get_user_model()


class LikeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reader", password="pw")
        self.category = Category.objects.create(name="소설")
        self.book = self.create_book("첫 번째 책")
        self.other_book = self.create_book("다른 책")
        self.thread = Thread.objects.create(
            title="읽은 기록",
            content="기억에 남는 문장",
            reading_date=date(2026, 1, 1),
            book=self.book,
            user=self.user,
        )
        self.client.force_login(self.user)
        self.like_url = reverse(
            "books:likes", args=[self.book.pk, self.thread.pk]
        )

    def create_book(self, title):
        return Book.objects.create(
            category=self.category,
            title=title,
            description="책 소개",
            isbn="9780000000000",
            cover="https://example.com/cover.jpg",
            publisher="출판사",
            pub_date=date(2025, 1, 1),
            author="작가",
            author_info="작가 소개",
            author_photo="https://example.com/author.jpg",
            customer_review_rank=8,
            subTitle="",
        )

    def test_like_and_unlike_toggle_and_return_total_count(self):
        response = self.client.post(self.like_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"is_liked": True, "likes_count": 1})
        self.assertEqual(self.thread.likes.count(), 1)

        response = self.client.post(self.like_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"is_liked": False, "likes_count": 0})
        self.assertEqual(self.thread.likes.count(), 0)

    def test_anonymous_and_mismatched_book_requests_are_rejected(self):
        self.client.logout()
        response = self.client.post(self.like_url)
        self.assertEqual(response.status_code, 401)

        self.client.force_login(self.user)
        mismatched_url = reverse(
            "books:likes", args=[self.other_book.pk, self.thread.pk]
        )
        response = self.client.post(mismatched_url)
        self.assertEqual(response.status_code, 404)

    def test_like_endpoint_only_accepts_post(self):
        response = self.client.get(self.like_url)
        self.assertEqual(response.status_code, 405)

    def test_thread_detail_displays_current_like_state(self):
        self.thread.likes.add(self.user)

        response = self.client.get(
            reverse("books:thread_detail", args=[self.book.pk, self.thread.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "좋아요 취소")
        self.assertContains(response, 'id="likes-count">1</span>')

        wrong_detail_url = reverse(
            "books:thread_detail", args=[self.other_book.pk, self.thread.pk]
        )
        self.assertEqual(self.client.get(wrong_detail_url).status_code, 404)


class CommentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="commenter", password="pw")
        self.other_user = User.objects.create_user(username="other", password="pw")
        self.category = Category.objects.create(name="에세이")
        self.book = self.create_book("댓글 대상 책")
        self.other_book = self.create_book("다른 책")
        self.thread = Thread.objects.create(
            title="독서 기록",
            content="본문",
            reading_date=date(2026, 1, 1),
            book=self.book,
            user=self.user,
        )
        self.client.force_login(self.user)
        self.create_url = reverse(
            "books:create_comment", args=[self.book.pk, self.thread.pk]
        )

    def create_book(self, title):
        return Book.objects.create(
            category=self.category,
            title=title,
            description="책 소개",
            isbn="9780000000000",
            cover="https://example.com/cover.jpg",
            publisher="출판사",
            pub_date=date(2025, 1, 1),
            author="작가",
            author_info="작가 소개",
            author_photo="https://example.com/author.jpg",
            customer_review_rank=8,
            subTitle="",
        )

    def test_create_comment_returns_data_for_dom_rendering(self):
        response = self.client.post(self.create_url, {"content": "기억하고 싶은 문장"})

        self.assertEqual(response.status_code, 201)
        comment = Comment.objects.get()
        self.assertEqual(comment.user, self.user)
        self.assertEqual(comment.thread, self.thread)
        self.assertEqual(response.json()["id"], comment.pk)
        self.assertEqual(response.json()["content"], "기억하고 싶은 문장")
        self.assertEqual(response.json()["username"], self.user.username)
        self.assertIn("delete_url", response.json())

    def test_blank_comment_and_mismatched_thread_are_rejected(self):
        response = self.client.post(self.create_url, {"content": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Comment.objects.count(), 0)

        wrong_book_url = reverse(
            "books:create_comment", args=[self.other_book.pk, self.thread.pk]
        )
        response = self.client.post(wrong_book_url, {"content": "댓글"})
        self.assertEqual(response.status_code, 404)

    def test_anonymous_comment_creation_is_rejected(self):
        self.client.logout()
        response = self.client.post(self.create_url, {"content": "댓글"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Comment.objects.count(), 0)

    def test_only_comment_author_can_delete(self):
        comment = Comment.objects.create(
            content="내가 작성한 댓글", thread=self.thread, user=self.user
        )
        delete_url = reverse("books:delete_comment", args=[self.book.pk, comment.pk])

        self.client.force_login(self.other_user)
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Comment.objects.filter(pk=comment.pk).exists())

        self.client.force_login(self.user)
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": True, "comment_id": comment.pk})
        self.assertFalse(Comment.objects.filter(pk=comment.pk).exists())

    def test_delete_requires_comment_to_belong_to_book(self):
        comment = Comment.objects.create(
            content="댓글", thread=self.thread, user=self.user
        )
        wrong_book_url = reverse(
            "books:delete_comment", args=[self.other_book.pk, comment.pk]
        )

        response = self.client.post(wrong_book_url)

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Comment.objects.filter(pk=comment.pk).exists())

    def test_comment_post_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        csrf_client.get(reverse("books:thread_detail", args=[self.book.pk, self.thread.pk]))

        response = csrf_client.post(self.create_url, {"content": "댓글"})
        self.assertEqual(response.status_code, 403)

        csrf_token = csrf_client.cookies["csrftoken"].value
        response = csrf_client.post(
            self.create_url,
            {"content": "댓글"},
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(response.status_code, 201)


class CategoryFilterTests(TestCase):
    def setUp(self):
        self.novel = Category.objects.create(name="소설")
        self.poetry = Category.objects.create(name="시")
        self.novel_book = self.create_book("소설 책", self.novel)
        self.poetry_book = self.create_book("시집", self.poetry)

    def create_book(self, title, category):
        return Book.objects.create(
            category=category,
            title=title,
            description="책 소개",
            isbn="9780000000000",
            cover="https://example.com/cover.jpg",
            publisher="출판사",
            pub_date=date(2025, 1, 1),
            author="작가",
            author_info="작가 소개",
            author_photo="https://example.com/author.jpg",
            customer_review_rank=8,
            subTitle="",
        )

    def test_index_is_public_and_renders_categories_and_all_books(self):
        response = self.client.get(reverse("books:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "소설")
        self.assertContains(response, self.novel_book.title)
        self.assertContains(response, self.poetry_book.title)
        self.assertEqual(len(response.context["books"]), 2)

    def test_category_filter_returns_matching_book_data(self):
        response = self.client.get(
            reverse("books:filter_category"), {"category": self.poetry.name}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["books"], [{
            "id": self.poetry_book.pk,
            "title": self.poetry_book.title,
            "author": self.poetry_book.author,
            "publisher": self.poetry_book.publisher,
            "customer_review_rank": self.poetry_book.customer_review_rank,
            "description": self.poetry_book.description,
            "cover": self.poetry_book.cover,
            "category": self.poetry.name,
        }])

    def test_empty_category_returns_all_and_unknown_category_returns_none(self):
        url = reverse("books:filter_category")
        all_response = self.client.get(url, {"category": ""})
        unknown_response = self.client.get(url, {"category": "없는 장르"})

        self.assertEqual(len(all_response.json()["books"]), 2)
        self.assertEqual(unknown_response.json()["books"], [])

    def test_filter_response_truncates_description_to_card_length(self):
        long_book = self.create_book("긴 소개", self.novel)
        long_book.description = "긴 소개 문장입니다. " * 30
        long_book.save(update_fields=["description"])

        response = self.client.get(
            reverse("books:filter_category"), {"category": self.novel.name}
        )
        book_data = next(
            item for item in response.json()["books"] if item["id"] == long_book.pk
        )

        self.assertLessEqual(len(book_data["description"]), 180)
        self.assertTrue(book_data["description"].endswith("…"))

    def test_catalog_and_filter_load_books_in_pages_of_24(self):
        for index in range(25):
            self.create_book(f"추가 도서 {index:02d}", self.novel)

        index_response = self.client.get(reverse("books:index"))
        self.assertEqual(len(index_response.context["books"]), 24)
        self.assertEqual(index_response.context["book_count"], 27)
        self.assertTrue(index_response.context["has_more_books"])

        filter_url = reverse("books:filter_category")
        first_page = self.client.get(filter_url, {"category": self.novel.name})
        second_page = self.client.get(
            filter_url, {"category": self.novel.name, "page": 2}
        )
        self.assertEqual(len(first_page.json()["books"]), 24)
        self.assertEqual(first_page.json()["total_count"], 26)
        self.assertTrue(first_page.json()["has_next"])
        self.assertEqual(len(second_page.json()["books"]), 2)
        self.assertFalse(second_page.json()["has_next"])

    def test_filter_endpoint_only_accepts_safe_requests(self):
        response = self.client.post(reverse("books:filter_category"))
        self.assertEqual(response.status_code, 405)


class RecommendationTests(TestCase):
    def setUp(self):
        self.novel = Category.objects.create(name="소설")
        self.history = Category.objects.create(name="역사")
        self.book = self.create_book("바다와 숲의 이야기", self.novel, "바다와 자연에 관한 이야기")
        self.same_category_book = self.create_book("소설 추천 책", self.novel, "다른 소개")
        self.other_category_book = self.create_book("바다 역사", self.history, "바다와 역사 이야기")
        cache.clear()

    def create_book(self, title, category, description):
        return Book.objects.create(
            category=category,
            title=title,
            description=description,
            isbn="9780000000000",
            cover="https://example.com/cover.jpg",
            publisher="출판사",
            pub_date=date(2025, 1, 1),
            author="작가",
            author_info="작가 소개",
            author_photo="https://example.com/author.jpg",
            customer_review_rank=8,
            subTitle="",
        )

    @patch("books.recommendations.os.getenv", return_value="")
    def test_missing_api_settings_use_explicit_category_fallback(self, _getenv):
        recommendations, mode = get_recommendations(self.book)

        self.assertEqual(mode, "category")
        self.assertEqual([book.pk for book in recommendations], [self.same_category_book.pk])
        self.assertNotIn(self.book.pk, [book.pk for book in recommendations])

    @patch("books.recommendations.os.getenv", return_value="configured")
    @patch("openai.OpenAI")
    def test_embeddings_rank_candidates_and_cache_result(self, openai_client, _getenv):
        client = openai_client.return_value
        client.embeddings.create.return_value.data = [
            SimpleNamespace(index=0, embedding=[1.0, 0.0]),
            SimpleNamespace(index=1, embedding=[0.0, 1.0]),
            SimpleNamespace(index=2, embedding=[1.0, 0.0]),
        ]

        recommendations, mode = get_recommendations(self.book)
        cached_recommendations, cached_mode = get_recommendations(self.book)

        self.assertEqual(mode, "ai")
        self.assertEqual(cached_mode, "ai")
        expected_ids = [self.other_category_book.pk, self.same_category_book.pk]
        self.assertEqual([book.pk for book in recommendations], expected_ids)
        self.assertEqual([book.pk for book in cached_recommendations], expected_ids)
        self.assertEqual(openai_client.call_count, 1)
        client.embeddings.create.assert_called_once()

    @patch("books.recommendations.os.getenv", return_value="configured")
    @patch("openai.OpenAI", side_effect=RuntimeError("API unavailable"))
    def test_api_failure_falls_back_without_repeating_request(self, openai_client, _getenv):
        recommendations, mode = get_recommendations(self.book)
        cached_recommendations, cached_mode = get_recommendations(self.book)

        self.assertEqual(mode, "category")
        self.assertEqual(cached_mode, "category")
        self.assertEqual([book.pk for book in recommendations], [self.same_category_book.pk])
        self.assertEqual([book.pk for book in cached_recommendations], [self.same_category_book.pk])
        self.assertEqual(openai_client.call_count, 1)

    @patch("books.views.get_recommendations")
    def test_book_detail_displays_recommendations_and_their_method(self, get_recommendations_mock):
        get_recommendations_mock.return_value = ([self.same_category_book], "category")

        response = self.client.get(reverse("books:detail", args=[self.book.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "카테고리 기반 추천")
        self.assertContains(response, self.same_category_book.title)
        self.assertNotContains(response, "AI 임베딩 유사도")
