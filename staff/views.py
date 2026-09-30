from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

# --- CRITICAL FIX: Import 'IsAdmin' here ---
from .permissions import IsAdmin, IsStaff
from .serializers import (
    StaffRegistrationSerializer,
    StaffProfileSerializer,
    StaffRolePermissionSerializer,
)
from .models import (
    NonTeachingStaff,
    StaffRolePermission,
    STAFF_PERMISSION_GROUPS,
    STAFF_PERMISSION_LABELS,
)
from school.tenant import get_requested_school, scope_queryset_for_user
from school.models import School

# --- 1. Admin Creates Staff ---
class CreateStaffView(APIView):
    """
    POST: Admin creates a new Non-Teaching Staff member.
    """
    # This will now work because IsAdmin is imported above
    permission_classes = [IsAuthenticated, IsAdmin] 

    def post(self, request):
        serializer = StaffRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({
                "message": "Staff Member Created Successfully",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# --- 2. Staff Views Their Own Profile ---
class StaffProfileView(APIView):
    """
    GET: Logged-in staff views their own details.
    """
    permission_classes = [IsAuthenticated, IsStaff] 

    def get(self, request):
        try:
            staff_member = request.user.staff_profile
            serializer = StaffProfileSerializer(staff_member)
            return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)
        except AttributeError:
            return Response(
                {"error": "Profile not found. Are you logged in as a Staff member?"}, 
                status=404
            )


class StaffRolePermissionView(APIView):
    permission_classes = [IsAuthenticated]

    def _can_admin_manage(self, request):
        return request.user.user_type in ('admin', 'super_admin')

    def _school_for_request(self, request):
        school = get_requested_school(request)
        if school:
            return school
        if request.user.user_type == 'super_admin':
            return School.objects.filter(is_active=True).first() or School.objects.first()
        return None

    def _setting(self, school, role):
        return StaffRolePermission.objects.get_or_create(school=school, role=role)[0]

    def get(self, request):
        if request.user.user_type == 'staff':
            staff = get_object_or_404(NonTeachingStaff, user=request.user)
            if not staff.school_id:
                return Response({"error": "Staff is not assigned to a school."}, status=400)
            serializer = StaffRolePermissionSerializer(self._setting(staff.school, staff.role))
            return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)

        if not self._can_admin_manage(request):
            return Response({"error": "Only staff and admins can access role permissions."}, status=403)

        school = self._school_for_request(request)
        if not school:
            return Response({"error": "Select a school before managing staff roles."}, status=400)

        role = request.query_params.get('role')
        roles = [choice[0] for choice in NonTeachingStaff.ROLE_CHOICES]
        if role:
            if role not in roles:
                return Response({"error": "Unknown staff role."}, status=400)
            serializer = StaffRolePermissionSerializer(self._setting(school, role))
            return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)

        settings = [self._setting(school, role_key) for role_key in roles]
        serializer = StaffRolePermissionSerializer(settings, many=True)
        return Response({
            "status": 200,
            "permission_groups": STAFF_PERMISSION_GROUPS,
            "permission_labels": STAFF_PERMISSION_LABELS,
            "roles": [{"id": value, "label": label} for value, label in NonTeachingStaff.ROLE_CHOICES],
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    def put(self, request):
        if not self._can_admin_manage(request):
            return Response({"error": "Only admins can update staff role permissions."}, status=403)

        school = self._school_for_request(request)
        if not school:
            return Response({"error": "Select a school before updating staff roles."}, status=400)

        role = request.data.get('role') or request.query_params.get('role')
        roles = [choice[0] for choice in NonTeachingStaff.ROLE_CHOICES]
        if role not in roles:
            return Response({"error": "Valid role is required."}, status=400)

        setting = self._setting(school, role)
        serializer = StaffRolePermissionSerializer(setting, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": 200,
                "message": f"Staff role permissions updated for {setting.get_role_display()}.",
                "data": serializer.data,
            }, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=400)

# ==========================================
# 3. ADMIN: View, Edit, or Delete Specific Staff
# ==========================================
class AdminStaffDetailView(APIView):
    """
    GET, PUT, DELETE: Admin manages a specific staff member.
    Requires query param: ?staff_id=STF001
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        target_id = request.query_params.get('staff_id')
        if not target_id: 
            return Response({"error": "staff_id parameter is required"}, status=400)
            
        staff_member = get_object_or_404(scope_queryset_for_user(NonTeachingStaff.objects.all(), request), staff_id=target_id) 
        serializer = StaffProfileSerializer(staff_member)
        return Response({"status": 200, "data": serializer.data}, status=status.HTTP_200_OK)

    def put(self, request):
        target_id = request.query_params.get('staff_id')
        if not target_id: 
            return Response({"error": "staff_id parameter is required"}, status=400)
            
        staff_member = get_object_or_404(scope_queryset_for_user(NonTeachingStaff.objects.all(), request), staff_id=target_id)
        
        # partial=True allows the Admin to update only specific fields
        serializer = StaffProfileSerializer(staff_member, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()  
            return Response({
                "status": 200, 
                "message": "Staff updated successfully", 
                "data": serializer.data
            }, status=status.HTTP_200_OK)
            
        return Response(serializer.errors, status=400)
        
    def delete(self, request):
        target_id = request.query_params.get('staff_id')
        if not target_id: 
            return Response({"error": "staff_id parameter is required"}, status=400)
            
        staff_member = get_object_or_404(scope_queryset_for_user(NonTeachingStaff.objects.all(), request), staff_id=target_id)
        staff_name = staff_member.name
        staff_member.delete()
        
        return Response({
            "status": 200, 
            "message": f"Staff member '{staff_name}' deleted successfully"
        }, status=status.HTTP_200_OK)


# ==========================================
# 3. ADMIN: List All Staff (With Role Filter)
# ==========================================
class AdminStaffListView(APIView):
    """
    GET: Admin views staff members. Can filter by role.
    Example: /api/staff/list/?role=it_staff
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        # 1. Look for 'role' in the URL query parameters
        role_filter = request.query_params.get('role')
        
        # 2. Start with ALL staff
        staff_queryset = scope_queryset_for_user(NonTeachingStaff.objects.all(), request)
        
        # 3. If a role was provided, filter the list!
        if role_filter:
            staff_queryset = staff_queryset.filter(role=role_filter)
            
        # 4. Serialize the data
        serializer = StaffProfileSerializer(staff_queryset, many=True)
        
        return Response({
            "status": 200, 
            "role_filtered": role_filter if role_filter else "All Roles",
            "count": staff_queryset.count(), 
            "data": serializer.data
        }, status=status.HTTP_200_OK)
