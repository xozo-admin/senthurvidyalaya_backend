# salary/models.py

from django.db import models
from django.conf import settings

class SalaryStructure(models.Model):
    STAFF_TYPE_CHOICES = (
        ('admin_staff', 'Admin Staff'),
        ('finance_staff', 'Finance Staff'),
        ('it_staff', 'IT Staff'),
        ('operations_staff', 'Operations Staff'),
        ('transport_staff', 'Transport Staff'),
        ('external_staff', 'External Staff'),
    )

    staff_type = models.CharField(max_length=50, choices=STAFF_TYPE_CHOICES, unique=True)
    base_salary = models.DecimalField(max_digits=10, decimal_places=2) 
    
    # We keep the Penalty % (Money logic), but removed the Time logic
    late_penalty_percentage = models.FloatField(default=10.0) 
    pending_base_salary = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pending_late_penalty_percentage = models.FloatField(null=True, blank=True)
    pending_effective_month = models.IntegerField(null=True, blank=True)
    pending_effective_year = models.IntegerField(null=True, blank=True)
    pending_delete = models.BooleanField(default=False)

    def get_effective_values(self, month: int, year: int):
        if (
            self.pending_delete
            and self.pending_effective_month
            and self.pending_effective_year
            and (year, month) >= (self.pending_effective_year, self.pending_effective_month)
        ):
            return None, None, True

        if (
            self.pending_base_salary is not None
            and self.pending_late_penalty_percentage is not None
            and self.pending_effective_month
            and self.pending_effective_year
            and (year, month) >= (self.pending_effective_year, self.pending_effective_month)
        ):
            return self.pending_base_salary, self.pending_late_penalty_percentage, False

        return self.base_salary, self.late_penalty_percentage, False

    def __str__(self):
        return f"{self.staff_type} - {self.base_salary}"


class TeacherSalaryStructure(models.Model):
    teacher_id = models.CharField(max_length=50, unique=True, help_text="Teacher ID (e.g., TCH001)")
    base_salary = models.DecimalField(max_digits=10, decimal_places=2) 
    late_penalty_percentage = models.FloatField(default=10.0) 
    pending_base_salary = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pending_late_penalty_percentage = models.FloatField(null=True, blank=True)
    pending_effective_month = models.IntegerField(null=True, blank=True)
    pending_effective_year = models.IntegerField(null=True, blank=True)
    pending_delete = models.BooleanField(default=False)

    def get_effective_values(self, month: int, year: int):
        if (
            self.pending_delete
            and self.pending_effective_month
            and self.pending_effective_year
            and (year, month) >= (self.pending_effective_year, self.pending_effective_month)
        ):
            return None, None, True

        if (
            self.pending_base_salary is not None
            and self.pending_late_penalty_percentage is not None
            and self.pending_effective_month
            and self.pending_effective_year
            and (year, month) >= (self.pending_effective_year, self.pending_effective_month)
        ):
            return self.pending_base_salary, self.pending_late_penalty_percentage, False

        return self.base_salary, self.late_penalty_percentage, False

    def __str__(self): 
        return f"{self.teacher_id} - {self.base_salary}"


# NEW: Staff Salary Payment Model
class StaffSalaryPayment(models.Model):
    PAYMENT_STATUS = (
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('processed', 'Processed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    )
    
    staff = models.ForeignKey('staff.NonTeachingStaff', on_delete=models.CASCADE, related_name='salary_payments')
    month = models.IntegerField()  # Month number (1-12)
    year = models.IntegerField()
    
    # Salary calculation fields
    base_salary = models.DecimalField(max_digits=10, decimal_places=2)
    per_day_wage = models.DecimalField(max_digits=10, decimal_places=2)
    days_worked = models.IntegerField()
    days_absent = models.IntegerField()
    days_late = models.IntegerField()
    
    # Deductions
    absent_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    late_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Final amount
    net_payable = models.DecimalField(max_digits=10, decimal_places=2)
    
    # Payment details
    payment_date = models.DateTimeField(null=True, blank=True)
    transaction_id = models.CharField(max_length=100, blank=True, null=True)
    bank_reference = models.CharField(max_length=100, blank=True, null=True)
    transfer_bank_code = models.CharField(max_length=20, default='DEFAULT')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default='pending')
    remarks = models.TextField(blank=True, null=True)
    
    # Metadata - FIXED: Use settings.AUTH_USER_MODEL instead of 'auth.User'
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,  # Changed from 'auth.User'
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='staff_payments_created'
    )
    
    class Meta:
        unique_together = ['staff', 'month', 'year']
        ordering = ['-year', '-month']
        
    def __str__(self):
        return f"{self.staff.name} - {self.month}/{self.year} - {self.net_payable}"


# NEW: Teacher Salary Payment Model
class TeacherSalaryPayment(models.Model):
    PAYMENT_STATUS = (
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('processed', 'Processed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    )
    
    teacher = models.ForeignKey('teachers.Teacher', on_delete=models.CASCADE, related_name='salary_payments')
    month = models.IntegerField()  # Month number (1-12)
    year = models.IntegerField()
    
    # Salary calculation fields
    base_salary = models.DecimalField(max_digits=10, decimal_places=2)
    per_day_wage = models.DecimalField(max_digits=10, decimal_places=2)
    days_worked = models.IntegerField()
    days_absent = models.IntegerField()
    days_late = models.IntegerField()
    
    # Deductions
    absent_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    late_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Final amount
    net_payable = models.DecimalField(max_digits=10, decimal_places=2)
    
    # Payment details
    payment_date = models.DateTimeField(null=True, blank=True)
    transaction_id = models.CharField(max_length=100, blank=True, null=True)
    bank_reference = models.CharField(max_length=100, blank=True, null=True)
    transfer_bank_code = models.CharField(max_length=20, default='DEFAULT')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default='pending')
    remarks = models.TextField(blank=True, null=True)
    
    # Metadata - FIXED: Use settings.AUTH_USER_MODEL instead of 'auth.User'
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,  # Changed from 'auth.User'
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='teacher_payments_created'
    )
    
    class Meta:
        unique_together = ['teacher', 'month', 'year']
        ordering = ['-year', '-month']
        
    def __str__(self):
        return f"{self.teacher.name} - {self.month}/{self.year} - {self.net_payable}"
    

class PaymentBatch(models.Model):
    """Track batch payment processing"""
    BATCH_STATUS = (
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('partially_completed', 'Partially Completed'),
    )
    
    batch_id = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='payment_batches_created'
    )
    total_payments = models.IntegerField()
    successful_payments = models.IntegerField(default=0)
    failed_payments = models.IntegerField(default=0)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2)
    status = models.CharField(max_length=20, choices=BATCH_STATUS, default='processing')
    results = models.JSONField()
    bank_code = models.CharField(max_length=20, default='DEFAULT')
    gateway_mode = models.CharField(max_length=10, default='DUMMY', 
                                   help_text='REAL or DUMMY mode')
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        
    def __str__(self):
        return f"{self.batch_id} - {self.created_at.date()} - {self.status}"
