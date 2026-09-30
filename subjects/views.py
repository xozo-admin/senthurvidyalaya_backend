from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from staff.permissions import IsOwnerAdminOrAdminStaff
from students.permissions import IsStudent

# Import Models & Serializers
from academics.models import Standard 
from .models import Subject         
from .serializers import BulkSubjectAssignSerializer, SubjectSerializer
from school.tenant import scope_queryset_for_user


def natural_class_key(standard):
    return (0, int(standard.name)) if str(standard.name).isdigit() else (1, str(standard.name).lower())

# --- 1. POST: Assign Subjects (Bulk) ---
class BulkAssignSubjectsView(APIView):
    """
    POST: Assign subjects to ONE or MULTIPLE classes.
    Supports both single object {} and list of objects [{}, {}].
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def post(self, request):
        # 1. Check if the data is a List (Multiple Classes) or Dictionary (Single Class)
        is_many = isinstance(request.data, list)

        # 2. Initialize Serializer
        serializer = BulkSubjectAssignSerializer(data=request.data, many=is_many, context={"request": request})

        if serializer.is_valid():
            # 3. Save (This creates the subjects in the DB)
            serializer.save()
            
            # --- FIX HERE ---
            # We do NOT return 'serializer.data' because it causes the "list object" error.
            # Instead, we return a success message and the input data.
            return Response({
                "status": 200,
                "message": "Subjects assigned successfully.",
                "assigned_data": request.data 
            }, status=status.HTTP_201_CREATED)
            
        return Response(serializer.errors, status=400)


# --- 2. GET: View Class Subjects ---
class ViewClassSubjects(APIView):
    # (Keep this class exactly as I gave you before)
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get(self, request):
        class_name = request.query_params.get('class')
        
        if not class_name:
            return Response({"error": "Please provide 'class' parameter (e.g., ?class=10)"}, status=400)

        try:
            standard = scope_queryset_for_user(Standard.objects.all(), request).get(name=class_name)
        except (Standard.DoesNotExist, Standard.MultipleObjectsReturned):
            return Response({"error": "Class not found"}, status=404)

        subjects = Subject.objects.filter(standard=standard).order_by('name')
        serializer = SubjectSerializer(subjects, many=True)

        return Response({
            "status": 200,
            "class": class_name,
            "subject_count": subjects.count(),
            "subjects": serializer.data
        }, status=status.HTTP_200_OK)


#### SIVA BRO SUBJECT URLS ## # # # 

class ViewAllClassSubjects(APIView):
    """
    GET: Get subjects for all classes.
    """
    permission_classes = [IsAuthenticated, IsOwnerAdminOrAdminStaff]

    def get(self, request):
        try:
            standards = sorted(scope_queryset_for_user(Standard.objects.all(), request), key=natural_class_key)

            if not standards:
                return Response({
                    "status": 200,
                    "message": "No classes found.",
                    "total_classes": 0,
                    "total_subjects": 0,
                    "classes": []
                }, status=status.HTTP_200_OK)

            classes_data = []
            total_subjects = 0

            for standard in standards:
                subjects = Subject.objects.filter(standard=standard).order_by('name')
                serializer = SubjectSerializer(subjects, many=True)
                subject_count = subjects.count()
                total_subjects += subject_count

                classes_data.append({
                    "class": standard.name,
                    "subject_count": subject_count,
                    "subjects": serializer.data,
                    "message": "No subjects assigned." if subject_count == 0 else "Subjects found."
                })

            return Response({
                "status": 200,
                "message": "All class subjects fetched successfully." if total_subjects > 0 else "No subjects assigned for any class.",
                "total_classes": len(standards),
                "total_subjects": total_subjects,
                "classes": classes_data
            }, status=status.HTTP_200_OK)

        except Exception:
            return Response({
                "status": 500,
                "error": "Failed to fetch class subjects."
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


#### SIVA BRO SUBJECT URLS ## # # # 

class StudentAllocatedSubjectsView(APIView):
    """
    GET: Get subjects for a student based on their class and section.
    Students can only access their own subjects.
    """
    permission_classes = [IsAuthenticated, IsStudent]
    
    def get(self, request):
        # Get student's information from request
        try:
            student_profile = request.user.student_profile
        except AttributeError:
            return Response(
                {"error": "Student profile not found. Please contact admin."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Use the correct field names from your Student model
        # CORRECTED: Use 'standard' instead of 'current_class'
        # CORRECTED: Use 'section' instead of 'current_section'
        student_standard = student_profile.standard  # ForeignKey to Standard
        student_section = student_profile.section    # ForeignKey to Section
        
        if not student_standard:
            return Response(
                {"error": "Class information not set. Please contact admin."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get all subjects for the student's class
        subjects = Subject.objects.filter(standard=student_standard)
        
        # Serialize the subjects
        serializer = SubjectSerializer(subjects, many=True)
        
        return Response({
            "status": 200,
            "message": "Subjects retrieved successfully",
            "student": {
                "name": f"{request.user.first_name} {request.user.last_name}",
                "student_id": student_profile.student_id,
                "class": student_standard.name,
                "section": student_section.name if student_section else "Not Assigned"
            },
            "subjects": serializer.data,
            "subject_count": subjects.count()
        }, status=status.HTTP_200_OK)
