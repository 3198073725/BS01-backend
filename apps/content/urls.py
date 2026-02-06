"""Content 应用路由模块。

用于组织内容相关 API 路由，例如话题、标签、素材等。
后续可在此导入 views 并定义 path 列表。
"""

from django.urls import path
from .views import TagListView, CategoryListView

app_name = 'content'

urlpatterns = [
    path('tags/', TagListView.as_view(), name='tag-list'),
    path('categories/', CategoryListView.as_view(), name='category-list'),
]
