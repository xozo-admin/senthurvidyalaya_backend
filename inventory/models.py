from django.db import models
from staff.models import NonTeachingStaff

class InventoryItem(models.Model):
    STAFF_TYPE_CHOICES = (
        ('admin_staff', 'Admin Staff'),
        ('finance_staff', 'Finance Staff'),
        ('it_staff', 'IT Staff'),
        ('operations_staff', 'Operations Staff'),
        ('transport_staff', 'Transport Staff'),
        ('external_staff', 'External Staff'),
    )

    stock_name = models.CharField(max_length=100) # e.g., "Floor Cleaner (Litres)"
    staff_type = models.CharField(max_length=50, choices=STAFF_TYPE_CHOICES)
    
    # We keep initial quantity to calculate "Percentage Left"
    initial_quantity = models.PositiveIntegerField(default=0) 
    current_quantity = models.PositiveIntegerField(default=0)
    
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.current_quantity} left)"

    @property
    def stock_status(self):
        """ Returns status based on percentage left """
        if self.initial_quantity == 0:
            return "Unknown"
        
        percentage = (self.current_quantity / self.initial_quantity) * 100
        
        if percentage == 0:
            return "Out of Stock"
        elif percentage < 25:
            return "Critical Low"
        elif percentage < 50:
            return "Low"
        return "Good"

class StockLog(models.Model):
    ACTION_CHOICES = (
        ('used', 'Used / Unsealed'),
        ('damaged', 'Damaged / Broken'),
        ('restocked', 'Restocked (Admin)'),
    )

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='logs')
    staff = models.ForeignKey(NonTeachingStaff, on_delete=models.SET_NULL, null=True) # If Admin does it, this might be null or admin profile
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    quantity_changed = models.IntegerField() # e.g., -1 or +10
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.item.name}: {self.action} by {self.staff.name if self.staff else 'Admin'}"