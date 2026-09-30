from rest_framework import serializers
from accounts.models import User
from school.models import School
from schooladmin.models import AdminProfile


# STEP 1 — ADMIN REGISTRATION
class AdminRegisterSerializer(serializers.Serializer):
    admin_name = serializers.CharField()
    admin_phone = serializers.CharField()
    admin_email = serializers.CharField()
    admin_password = serializers.CharField()

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data["admin_email"],  # admin email = login username
            email=validated_data["admin_email"],
            password=validated_data["admin_password"],
            user_type="admin",
            phone=validated_data["admin_phone"],
            first_name=validated_data["admin_name"]
        )
        return user


class SuperAdminRegisterSerializer(serializers.Serializer):
    name = serializers.CharField()
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField()
    password = serializers.CharField()

    def validate_email(self, value):
        if User.objects.filter(username__iexact=value).exists() or User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data["email"],
            email=validated_data["email"],
            password=validated_data["password"],
            user_type="super_admin",
            phone=validated_data.get("phone", ""),
            first_name=validated_data["name"],
            is_staff=True,
        )


class AdminCreateSerializer(serializers.Serializer):
    admin_name = serializers.CharField()
    admin_phone = serializers.CharField(required=False, allow_blank=True)
    admin_email = serializers.EmailField()
    admin_password = serializers.CharField(write_only=True)
    school_id = serializers.IntegerField(required=True)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_admin_email(self, value):
        if User.objects.filter(username__iexact=value).exists() or User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_school_id(self, value):
        if not School.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError("Please choose an active school.")
        return value

    def create(self, validated_data):
        school_id = validated_data.pop("school_id")
        user = User.objects.create_user(
            username=validated_data["admin_email"],
            email=validated_data["admin_email"],
            password=validated_data["admin_password"],
            user_type="admin",
            phone=validated_data.get("admin_phone", ""),
            first_name=validated_data["admin_name"],
            is_active=validated_data.get("is_active", True),
        )
        AdminProfile.objects.create(user=user, school_id=school_id)
        return user


# STEP 2 — SCHOOL REGISTRATION
class SchoolRegisterSerializer(serializers.Serializer):
    institution = serializers.IntegerField(required=False, allow_null=True)
    school_name = serializers.CharField()
    code = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    contact_phone = serializers.CharField(required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    total_students = serializers.IntegerField()
    total_staffs = serializers.IntegerField()
    total_teachers = serializers.IntegerField()
    total_non_teaching = serializers.IntegerField(required=False)

    def validate(self, data):
        if data["total_teachers"] > data["total_staffs"]:
            raise serializers.ValidationError("Teachers cannot exceed total staffs")
        return data

    def create(self, validated_data):
        # Calculate non-teaching staff if not provided
        non_teaching = validated_data["total_staffs"] - validated_data["total_teachers"]

        school = School.objects.create(
            institution_id=validated_data.get("institution"),
            name=validated_data["school_name"],
            code=validated_data.get("code") or None,
            address=validated_data.get("address", ""),
            contact_phone=validated_data.get("contact_phone", ""),
            contact_email=validated_data.get("contact_email", ""),
            total_students=validated_data["total_students"],
            total_staffs=validated_data["total_staffs"],
            total_teachers=validated_data["total_teachers"],
            total_non_teaching=non_teaching
        )
        return school
