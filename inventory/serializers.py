from rest_framework import serializers
from .models import InventoryItem, StockLog

class StockLogSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.name', read_only=True)
    staff_id = serializers.CharField(source='staff.staff_id', read_only=True)

    class Meta:
        model = StockLog
        fields = ['id', 'staff_name', 'staff_id', 'action', 'quantity_changed', 'timestamp']

class InventoryItemSerializer(serializers.ModelSerializer):
    # Determine 'Low Stock' label dynamically
    status = serializers.ReadOnlyField(source='stock_status')
    logs = StockLogSerializer(many=True, read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            'id', 
            'stock_name', 
            'staff_type', 
            'initial_quantity', 
            'current_quantity', 
            'status', # e.g., "Critical Low"
            'last_updated',
            'logs'
        ]