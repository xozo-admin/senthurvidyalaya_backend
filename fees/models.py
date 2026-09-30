# fees/models.py - Update FeePayment model

from django.db import models
from students.models import Student
from academics.models import Standard, Section
import uuid
from decimal import Decimal, ROUND_DOWN

class FeeDefinition(models.Model):
    """
    Rule Book: Admin sets fee for a Standard.
    Example: Class 10 - Tuition Fee - 35000
    """
    academic_year = models.CharField(max_length=20)  # e.g., "2024-2025"
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE)
    fee_type = models.CharField(max_length=50)  # Tuition, Transport, Hostel
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    installment_count = models.PositiveIntegerField(default=1)
    due_date = models.DateField()
    is_active = models.BooleanField(default=True)  # Soft delete

    class Meta:
        unique_together = ('academic_year', 'standard', 'fee_type')

    def __str__(self):
        return f"{self.standard.name} - {self.fee_type} ({self.academic_year})"


class StudentFee(models.Model):
    """
    The Ledger: Tracks how much a specific student owes and has paid.
    """
    STATUS_CHOICES = [
        ('PAID', 'Paid'),
        ('UNPAID', 'Unpaid'),
        ('OVERDUE', 'Overdue'),
        ('REFUNDED', 'Refunded')
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='fees')
    fee_definition = models.ForeignKey(FeeDefinition, on_delete=models.CASCADE)
    
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # New Field for Discounts
    concession_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # Payment tracking
    last_payment_date = models.DateField(null=True, blank=True)
    due_reminder_sent = models.BooleanField(default=False)
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='UNPAID')

    @property
    def due_amount(self):
        # Logic: max(Total - (Paid + Discount), 0)
        due = self.total_amount - (self.paid_amount + self.concession_amount)
        return due if due > 0 else Decimal('0.00')

    @property
    def effective_total(self):
        """Total after applying concession"""
        return self.total_amount - self.concession_amount

    @property
    def installment_count(self):
        return max(int(getattr(self.fee_definition, 'installment_count', 1) or 1), 1)

    @property
    def installments_paid_count(self):
        return self.payments.filter(payment_status='SUCCESS').count()

    def get_installment_schedule(self):
        installment_count = self.installment_count
        effective_total = Decimal(self.effective_total or Decimal('0.00'))
        if installment_count <= 1:
            return [effective_total.quantize(Decimal('0.01'))]

        per_installment = (effective_total / installment_count).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        schedule = [per_installment for _ in range(installment_count)]
        allocated = per_installment * installment_count
        remainder = (effective_total - allocated).quantize(Decimal('0.01'))
        cents = int((remainder * 100).to_integral_value())
        for index in range(cents):
            schedule[index] += Decimal('0.01')
        return schedule

    @property
    def installment_amount(self):
        schedule = self.get_installment_schedule()
        return schedule[0] if schedule else Decimal('0.00')

    @property
    def next_installment_number(self):
        return min(self.installments_paid_count + 1, self.installment_count)

    @property
    def next_installment_amount(self):
        paid_count = self.installments_paid_count
        if self.due_amount <= Decimal('0.00'):
            return Decimal('0.00')
        if paid_count >= self.installment_count - 1:
            return self.due_amount.quantize(Decimal('0.01'))
        schedule = self.get_installment_schedule()
        return schedule[min(paid_count, len(schedule) - 1)].quantize(Decimal('0.01'))

    @property
    def installments_remaining_count(self):
        return max(self.installment_count - self.installments_paid_count, 0)

    def update_status(self):
        """Update status based on payments and due date"""
        effective_total = self.effective_total
        
        if self.paid_amount >= effective_total:
            self.status = 'PAID'
        else:
            self.status = 'UNPAID'
        
        # Check for overdue
        from django.utils import timezone
        if self.fee_definition.due_date < timezone.now().date() and self.status != 'PAID':
            self.status = 'OVERDUE'

    def __str__(self):
        return f"{self.student.student_name} - {self.fee_definition.fee_type}"


class FeePayment(models.Model):
    """
    The Receipt: Individual transactions (installments) with Razorpay integration
    """
    PAYMENT_STATUS = [
        ('INITIATED', 'Initiated'),
        ('PENDING', 'Pending'),
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
        ('REFUND_PENDING', 'Refund Pending')
    ]
    
    PAYMENT_MODE = [
        ('CASH', 'Cash'),
        ('UPI', 'UPI'),
        ('CARD', 'Card'),
        ('NETBANKING', 'Net Banking'),
        ('RAZORPAY', 'Razorpay'),
        ('CHEQUE', 'Cheque'),
        ('DD', 'Demand Draft'),
        ('ONLINE', 'Online Transfer')
    ]

    student_fee = models.ForeignKey(StudentFee, on_delete=models.CASCADE, related_name='payments')
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    installment_number = models.PositiveIntegerField(default=1)
    payment_mode = models.CharField(max_length=20, choices=PAYMENT_MODE)
    transaction_id = models.CharField(max_length=100, unique=True)
    payment_date = models.DateField(auto_now_add=True)
    
    # Razorpay specific fields
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=200, blank=True, null=True)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default='SUCCESS')
    refunded_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # Store snapshot of student details for the receipt
    remarks = models.TextField(blank=True, null=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['-payment_date', '-created_at']
        indexes = [
            models.Index(fields=['transaction_id']),
            models.Index(fields=['razorpay_order_id']),
            models.Index(fields=['payment_status']),
            models.Index(fields=['payment_date']),
        ]

    def __str__(self):
        return f"{self.transaction_id} - {self.amount_paid}"

    def save(self, *args, **kwargs):
        # Run ID initialization only once on first insert.
        if self._state.adding and not self.transaction_id:
            self.transaction_id = self.generate_transaction_id()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_transaction_id():
        """Generate unique transaction ID"""
        return f"TXN{uuid.uuid4().hex[:12].upper()}"


class FeeRefundEvent(models.Model):
    """
    Idempotency tracker for gateway refund webhooks.
    """
    payment = models.ForeignKey(FeePayment, on_delete=models.CASCADE, related_name='refund_events')
    refund_id = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class FeeReminder(models.Model):
    """
    Track fee reminders sent to parents/students
    """
    student_fee = models.ForeignKey(StudentFee, on_delete=models.CASCADE, related_name='reminders')
    reminder_date = models.DateTimeField(auto_now_add=True)
    reminder_type = models.CharField(max_length=20, choices=[
        ('EMAIL', 'Email'),
        ('SMS', 'SMS'),
        ('PUSH', 'Push Notification'),
        ('WHATSAPP', 'WhatsApp')
    ])
    sent_to = models.CharField(max_length=100)  # Email/Phone
    status = models.CharField(max_length=20, default='SENT')
    response_data = models.JSONField(default=dict, blank=True)
    
    class Meta:
        ordering = ['-reminder_date']


class FeeConcession(models.Model):
    """
    Track concessions/discounts applied to students
    """
    CONCESSION_TYPE = [
        ('SCHOLARSHIP', 'Scholarship'),
        ('SIBLING', 'Sibling Discount'),
        ('STAFF', 'Staff Child'),
        ('MERIT', 'Merit Based'),
        ('OTHER', 'Other')
    ]
    
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='concessions')
    fee_definition = models.ForeignKey(FeeDefinition, on_delete=models.CASCADE)
    concession_type = models.CharField(max_length=20, choices=CONCESSION_TYPE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    percentage = models.FloatField(null=True, blank=True)  # If percentage based
    approved_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True)
    valid_from = models.DateField()
    valid_to = models.DateField()
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-created_at']
