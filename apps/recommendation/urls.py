"""Recommendation 应用路由模块。

用于组织推荐相关 API 路由，例如推荐流、个性化召回等。
"""

from django.urls import path
from . import views

app_name = 'recommendation'

urlpatterns = [
    path('feed/', views.RecommendationFeedView.as_view(), name='feed'),
    path('following/', views.FollowingFeedView.as_view(), name='following'),
    path('featured/', views.FeaturedFeedView.as_view(), name='featured'),
]
