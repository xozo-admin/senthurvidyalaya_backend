# fees/serializers.py

from rest_framework import serializers
from .models import FeeDefinition, StudentFee, FeePayment, FeeConcession, FeeReminder
from students.models import Student
from academics.models import Standard

# --- 1. RECEIPT SERIALIZER (For Student View) ---
class PaymentReceiptSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student_fee.student.student_name')
    student_id = serializers.CharField(source='student_fee.student.student_id')
    student_class = serializers.CharField(source='student_fee.student.section.standard.name')
    student_section = serializers.CharField(source='student_fee.student.section.name')
    fee_type = serializers.CharField(source='student_fee.fee_definition.fee_type')
    academic_year = serializers.CharField(source='student_fee.fee_definition.academic_year')
    total_fee = serializers.DecimalField(source='student_fee.total_amount', max_digits=10, decimal_places=2)
    concession_amount = serializers.DecimalField(source='student_fee.concession_amount', max_digits=10, decimal_places=2)
    balance_due = serializers.DecimalField(source='student_fee.due_amount', max_digits=10, decimal_places=2)
    installment_count = serializers.IntegerField(source='student_fee.fee_definition.installment_count', read_only=True)
    installment_number = serializers.IntegerField(read_only=True)
    next_installment_amount = serializers.DecimalField(source='student_fee.next_installment_amount', max_digits=10, decimal_places=2, read_only=True)
    payment_datetime = serializers.SerializerMethodField()
    status = serializers.CharField(source='student_fee.status')
    payment_mode_display = serializers.CharField(source='get_payment_mode_display')
    payment_status_display = serializers.CharField(source='get_payment_status_display')

    class Meta:
        model = FeePayment
        fields = [
            'id', 'student_id', 'student_name', 'student_class', 'student_section',
            'fee_type', 'academic_year', 'transaction_id', 'payment_mode', 
            'payment_mode_display', 'payment_date', 'payment_datetime', 'amount_paid', 
            'total_fee', 'concession_amount', 'balance_due', 'status',
            'installment_count', 'installment_number', 'next_installment_amount',
            'payment_status', 'payment_status_display', 'razorpay_order_id',
            'razorpay_payment_id', 'remarks'
        ]

    def get_payment_datetime(self, obj):
        from django.utils import timezone
        return timezone.localtime(obj.created_at).isoformat()


# --- 2. CLASS REPORT SERIALIZER (For Admin View) ---
class StudentFeeReportSerializer(serializers.ModelSerializer):
    student_id = serializers.CharField(source='student.student_id')
    student_name = serializers.CharField(source='student.student_name')
    student_roll = serializers.SerializerMethodField()
    student_class = serializers.CharField(source='student.section.standard.name', read_only=True)
    student_section = serializers.CharField(source='student.section.name', read_only=True)
    parent_mobile = serializers.SerializerMethodField()
    due_amount = serializers.SerializerMethodField()
    paid_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    concession_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    effective_total = serializers.SerializerMethodField()
    installment_count = serializers.IntegerField(source='fee_definition.installment_count', read_only=True)
    next_installment_amount = serializers.SerializerMethodField()
    installments_count = serializers.SerializerMethodField()
    installments_remaining_count = serializers.SerializerMethodField()
    last_payment_date = serializers.DateField()
    payment_status = serializers.CharField(source='status')
    last_payment = serializers.SerializerMethodField()

    class Meta:
        model = StudentFee
        fields = [
            'student_id', 'student_name', 'student_roll', 'parent_mobile',
            'student_class', 'student_section',
            'total_amount', 'concession_amount', 'effective_total',
            'paid_amount', 'due_amount', 'payment_status', 'installment_count',
            'next_installment_amount', 'installments_count', 'installments_remaining_count',
            'last_payment_date', 'last_payment'
        ]

    def get_due_amount(self, obj):
        return obj.due_amount

    def get_student_roll(self, obj):
        extra = obj.student.extra_details if isinstance(obj.student.extra_details, dict) else {}
        return extra.get('roll_number', '')

    def get_parent_mobile(self, obj):
        return obj.student.father_phone or obj.student.mother_phone or ''
    
    def get_effective_total(self, obj):
        return obj.effective_total
    
    def get_installments_count(self, obj):
        return obj.payments.count()

    def get_next_installment_amount(self, obj):
        return obj.next_installment_amount

    def get_installments_remaining_count(self, obj):
        return obj.installments_remaining_count
    
    def get_last_payment(self, obj):
        last_payment = obj.payments.order_by('-payment_date').first()
        if last_payment:
            return {
                'date': last_payment.payment_date,
                'amount': last_payment.amount_paid,
                'mode': last_payment.get_payment_mode_display(),
                'transaction_id': last_payment.transaction_id
            }
        return None


# --- 3. FEE STRUCTURE SERIALIZER ---
class FeeDefinitionSerializer(serializers.ModelSerializer):
    class_name = serializers.CharField(source='standard.name', read_only=True)
    total_students = serializers.SerializerMethodField()
    paid_count = serializers.SerializerMethodField()
    total_collected = serializers.SerializerMethodField()
    installment_amount = serializers.SerializerMethodField()

    class Meta:
        model = FeeDefinition
        fields = [
            'id', 'academic_year', 'fee_type', 'class_name', 
            'amount', 'installment_count', 'installment_amount', 'due_date', 'is_active', 'total_students',
            'paid_count', 'total_collected'
        ]

    def get_total_students(self, obj):
        return Student.objects.filter(section__standard=obj.standard).count()
    
    def get_paid_count(self, obj):
        return StudentFee.objects.filter(
            fee_definition=obj,
            status='PAID'
        ).count()
    
    def get_total_collected(self, obj):
        from django.db.models import Sum
        result = FeePayment.objects.filter(
            student_fee__fee_definition=obj,
            payment_status='SUCCESS'
        ).aggregate(total=Sum('amount_paid'))
        return result['total'] or 0

    def get_installment_amount(self, obj):
        from decimal import Decimal, ROUND_DOWN

        installment_count = max(int(getattr(obj, 'installment_count', 1) or 1), 1)
        if installment_count <= 1:
            return obj.amount
        per_installment = (Decimal(obj.amount) / installment_count).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        allocated = per_installment * installment_count
        remainder = Decimal(obj.amount) - allocated
        return (per_installment + remainder).quantize(Decimal('0.01')) if remainder > 0 else per_installment


# --- 4. PAYMENT INITIATION SERIALIZER ---
class PaymentInitiationSerializer(serializers.Serializer):
    student_id = serializers.CharField(required=True)
    class_name = serializers.CharField(required=False, allow_blank=True)
    academic_year = serializers.CharField(required=True)
    fee_type = serializers.CharField(required=True)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=True)
    payment_mode = serializers.ChoiceField(
        choices=['CASH', 'UPI', 'CARD', 'NETBANKING', 'RAZORPAY'], 
        required=True
    )
    return_url = serializers.URLField(required=False, allow_blank=True)


# --- 5. PAYMENT VERIFICATION SERIALIZER ---
class PaymentVerificationSerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField(required=True)
    razorpay_payment_id = serializers.CharField(required=True)
    razorpay_signature = serializers.CharField(required=True)


# --- 6. CONCESSION SERIALIZER ---
class FeeConcessionSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.student_name', read_only=True)
    student_id = serializers.CharField(source='student.student_id', read_only=True)
    class_name = serializers.CharField(source='fee_definition.standard.name', read_only=True)
    fee_type = serializers.CharField(source='fee_definition.fee_type', read_only=True)
    academic_year = serializers.CharField(source='fee_definition.academic_year', read_only=True)

    class Meta:
        model = FeeConcession
        fields = [
            'id', 'student', 'student_id', 'student_name', 'fee_definition',
            'class_name', 'fee_type', 'academic_year', 'concession_type',
            'amount', 'percentage', 'approved_by', 'valid_from', 'valid_to',
            'reason', 'created_at', 'is_active'
        ]
        read_only_fields = ['created_at', 'approved_by']


# --- 7. BULK FEE ASSIGNMENT SERIALIZER ---
class BulkFeeAssignmentSerializer(serializers.Serializer):
    academic_year = serializers.CharField(required=True)
    class_name = serializers.CharField(required=False, allow_blank=True)
    fee_type = serializers.CharField(required=True)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=True)
    installment_count = serializers.IntegerField(required=False, min_value=1, default=1)
    due_date = serializers.DateField(required=True)
    override_existing = serializers.BooleanField(default=False)


# --- 8. DUE DATE EXTENSION SERIALIZER ---
class DueDateExtensionSerializer(serializers.Serializer):
    fee_definition_id = serializers.IntegerField(required=True)
    new_due_date = serializers.DateField(required=True)
    reason = serializers.CharField(required=True)
