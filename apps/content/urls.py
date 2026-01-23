"""Content 应用路由模块。

用于组织内容相关 API 路由，例如话题、标签、素材等。
后续可在此导入 views 并定义 path 列表。
"""

from django.urls import path

app_name = 'content'

urlpatterns = [
    # 示例：
    # path('tags/', views.TagListView.as_view(), name='tag-list'),
]
