from django.urls import path
from .views import (
    StandardListCreateView, 
    SectionListCreateView, 
    SetupSectionView,       # <--- Added missing import
    BulkCreateStandardsView,  # <--- NEW IMPORT
    BulkAssignSectionsView,    # <--- NEW IMPORT
    SchoolStructureView,    # <--- NEW IMPORT
    StructureEditView,       # <--- NEW IMPORT
    StandardListCreateViewNew
)

urlpatterns = [
    # 1. List/Create Classes (Standards)
    path('standards/', StandardListCreateView.as_view(), name='standard-list'),
    
    # 2. List/Create Sections (Existing Logic)
    path('sections/', SectionListCreateView.as_view(), name='section-list'),
    
    # 3. Setup New Section (The new API)
    # Changed path to 'setup/sections/' to avoid conflict with the line above
    path('setup/sections/', SetupSectionView.as_view(), name='setup-sections'),

    # 4. Bulk Create Standards (Screen 1: "1, 2, 3...")
    path('setup/standards/bulk/', BulkCreateStandardsView.as_view(), name='bulk-create-standards'),

    # 5. Bulk Assign Sections (Screen 2: "One Shot Save")
    path('setup/sections/bulk-map/', BulkAssignSectionsView.as_view(), name='bulk-map-sections'),

    # 6. MASTER VIEW & DELETE (Get Hierarchy / Smart Delete)
    path('structure/manage/', SchoolStructureView.as_view()),

    # 7. EDIT STRUCTURE (Rename Sections/Subjects)
    path('structure/edit/', StructureEditView.as_view()),

    # -- siva --

             path('standards-new/', StandardListCreateViewNew.as_view(), name='standard-list'),
]