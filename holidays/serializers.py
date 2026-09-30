from rest_framework import serializers
from .models import Holiday

class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = '__all__'

# NEW: For validating bulk creation input
class BulkHolidaySerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    applicable_for = serializers.ChoiceField(choices=Holiday.APPLICABLE_CHOICES)
    dates = serializers.ListField(
        child=serializers.DateField(),
        allow_empty=False,
        help_text="List of dates (YYYY-MM-DD)"
    )