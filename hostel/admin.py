from django.contrib import admin

from .models import (
    HostelAllocation,
    HostelAttendance,
    HostelBed,
    HostelBlock,
    HostelIncident,
    HostelInOutLog,
    HostelRoom,
    HostelWardenAssignment,
)

admin.site.register(HostelBlock)
admin.site.register(HostelRoom)
admin.site.register(HostelBed)
admin.site.register(HostelWardenAssignment)
admin.site.register(HostelAllocation)
admin.site.register(HostelAttendance)
admin.site.register(HostelIncident)
admin.site.register(HostelInOutLog)
