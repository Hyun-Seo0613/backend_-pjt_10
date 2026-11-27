from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse


User = get_user_model()


class FollowTests(TestCase):
    def setUp(self):
        self.reader = User.objects.create_user(username="reader", password="pw")
        self.author = User.objects.create_user(username="author", password="pw")
        self.follow_url = reverse("accounts:follow", args=[self.author.pk])
        self.client.force_login(self.reader)

    def test_follow_and_unfollow_update_relationship_and_counts(self):
        response = self.client.post(self.follow_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "is_following": True,
            "followers_count": 1,
            "followings_count": 0,
        })
        self.assertTrue(self.reader.followings.filter(pk=self.author.pk).exists())

        response = self.client.post(self.follow_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "is_following": False,
            "followers_count": 0,
            "followings_count": 0,
        })
        self.assertFalse(self.reader.followings.filter(pk=self.author.pk).exists())

    def test_anonymous_and_self_follow_requests_are_rejected(self):
        self.client.logout()
        response = self.client.post(self.follow_url)
        self.assertEqual(response.status_code, 401)

        self.client.force_login(self.reader)
        response = self.client.post(reverse("accounts:follow", args=[self.reader.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.reader.followings.exists())

    def test_follow_endpoint_only_accepts_post(self):
        response = self.client.get(self.follow_url)
        self.assertEqual(response.status_code, 405)

    def test_profile_shows_saved_follow_state_and_counts(self):
        self.reader.followings.add(self.author)

        response = self.client.get(reverse("accounts:profile", args=[self.author.username]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="followers-count">1</strong>')
        self.assertContains(response, 'id="followings-count">0</strong>')
        self.assertContains(response, "팔로우 취소")

    def test_follow_post_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.reader)
        csrf_client.get(reverse("accounts:profile", args=[self.author.username]))

        response = csrf_client.post(self.follow_url)
        self.assertEqual(response.status_code, 403)

        csrf_token = csrf_client.cookies["csrftoken"].value
        response = csrf_client.post(self.follow_url, HTTP_X_CSRFTOKEN=csrf_token)
        self.assertEqual(response.status_code, 200)
