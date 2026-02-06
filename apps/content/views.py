"""Content 视图模块。

提供标签与分类的只读列表接口，供前台内容编辑时选择使用。
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from rest_framework.exceptions import ValidationError, PermissionDenied
from django.db.models import Q

from backend.common.pagination import StandardResultsSetPagination
from .models import Tag, Category


class TagListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        qs = Tag.objects.all()
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(name__icontains=q)
        qs = qs.order_by('name')
        p = StandardResultsSetPagination()
        rows = list(p.paginate_queryset(qs, request, view=self))
        total = p.page.paginator.count
        data = [{'id': str(t.id), 'name': t.name} for t in rows]
        return Response(p.format(data, total))

    def post(self, request):
        # require auth for creation
        u = getattr(request, 'user', None)
        if not (u and getattr(u, 'is_authenticated', False)):
            raise PermissionDenied('未登录')
        name = (request.data.get('name') or '').strip()
        if not name:
            raise ValidationError({'name': '标签名称不能为空'})
        if len(name) > 50:
            raise ValidationError({'name': '长度不能超过 50'})
        # dedupe by case-insensitive match
        exist = Tag.objects.filter(name__iexact=name).first()
        if exist:
            return Response({'id': str(exist.id), 'name': exist.name})
        t = Tag.objects.create(name=name)
        return Response({'id': str(t.id), 'name': t.name})


class CategoryListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        qs = Category.objects.all().order_by('name')
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
        p = StandardResultsSetPagination()
        rows = list(p.paginate_queryset(qs, request, view=self))
        total = p.page.paginator.count
        data = [{'id': str(c.id), 'name': c.name, 'description': c.description} for c in rows]
        return Response(p.format(data, total))
