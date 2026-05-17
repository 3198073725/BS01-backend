"""
基于角色的权限系统 (RBAC)
"""
from rest_framework import permissions


class IsAdminUserWithRole(permissions.BasePermission):
    """
    基础管理员权限，要求用户是 staff 且具有指定角色
    """
    required_roles = ['reviewer', 'moderator', 'admin', 'super_admin']
    
    def has_permission(self, request, view):
        return (
            request.user and 
            request.user.is_authenticated and 
            getattr(request.user, 'is_staff', False) and
            getattr(request.user, 'admin_role', 'none') in self.required_roles
        )


class IsReviewer(IsAdminUserWithRole):
    """
    审核员权限：可查看、记录警告
    """
    required_roles = ['reviewer', 'moderator', 'admin', 'super_admin']


class IsModerator(IsAdminUserWithRole):
    """
    版主权限：可删除内容
    """
    required_roles = ['moderator', 'admin', 'super_admin']


class IsAdmin(IsAdminUserWithRole):
    """
    管理员权限：可封禁用户
    """
    required_roles = ['admin', 'super_admin']


class IsSuperAdmin(IsAdminUserWithRole):
    """
    超级管理员权限：全部操作
    """
    required_roles = ['super_admin']


class CanHandleReport(permissions.BasePermission):
    """
    举报处理权限检查
    根据动作类型判断是否有权限
    """
    def has_permission(self, request, view):
        if not (
            request.user and 
            request.user.is_authenticated and 
            getattr(request.user, 'is_staff', False)
        ):
            return False
        
        role = getattr(request.user, 'admin_role', 'none')
        
        # 获取请求中的动作类型
        action = request.data.get('action') if request.data else None
        
        if not action:
            # 如果没有指定动作，至少允许查看
            return role in ['reviewer', 'moderator', 'admin', 'super_admin']
        
        # 根据动作判断权限
        action_permissions = {
            'dismiss': ['reviewer', 'moderator', 'admin', 'super_admin'],
            'warn': ['reviewer', 'moderator', 'admin', 'super_admin'],
            'escalate': ['reviewer', 'moderator', 'admin', 'super_admin'],
            'delete_content': ['moderator', 'admin', 'super_admin'],
            'ban_user': ['admin', 'super_admin'],
        }
        
        allowed_roles = action_permissions.get(action, [])
        return role in allowed_roles
