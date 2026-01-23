"""Analytics 应用测试：冒烟用例。"""

from django.test import TestCase


class TestSmoke(TestCase):
    def test_truth(self):
        self.assertTrue(True)
