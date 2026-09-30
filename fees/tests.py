from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import User
from academics.models import Standard, Section
from students.models import Student
from .models import FeeConcession, FeeDefinition, FeePayment, FeeRefundEvent, StudentFee
from .serializers import PaymentInitiationSerializer
from .views import ApplyConcessionView, RazorpayWebhookView, _resolve_active_fee_definition


class FeesFlowTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin_user = User.objects.create_user(
            username="adminfees",
            password="password123",
            user_type="admin"
        )

        self.standard = Standard.objects.create(name="10", description="Class 10")
        self.section = Section.objects.create(standard=self.standard, name="A")
        self.student = Student.objects.create(
            student_id="STU-TEST-1",
            student_name="Test Student",
            student_email="student@example.com",
            father_name="Father",
            mother_name="Mother",
            father_phone="9999999999",
            mother_phone="8888888888",
            date_of_birth="2010-01-01",
            gender="M",
            standard=self.standard,
            section=self.section,
        )

    def test_payment_initiation_serializer_requires_academic_year(self):
        serializer = PaymentInitiationSerializer(data={
            "student_id": self.student.student_id,
            "class_name": self.standard.name,
            "fee_type": "Tuition",
            "amount": "100.00",
            "payment_mode": "RAZORPAY",
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("academic_year", serializer.errors)

    def test_resolve_active_fee_definition_scoped_by_year(self):
        FeeDefinition.objects.create(
            academic_year="2025-2026",
            standard=self.standard,
            fee_type="Tuition",
            amount=Decimal("5000.00"),
            due_date="2026-03-01",
            is_active=True,
        )
        current = FeeDefinition.objects.create(
            academic_year="2026-2027",
            standard=self.standard,
            fee_type="Tuition",
            amount=Decimal("6000.00"),
            due_date="2027-03-01",
            is_active=True,
        )

        fee_def, err = _resolve_active_fee_definition(self.standard, "Tuition", "2026-2027")
        self.assertIsNone(err)
        self.assertEqual(fee_def.id, current.id)

    def test_concession_put_blocks_amount_above_total_fee(self):
        fee_def = FeeDefinition.objects.create(
            academic_year="2026-2027",
            standard=self.standard,
            fee_type="Tuition",
            amount=Decimal("5000.00"),
            due_date="2027-03-01",
            is_active=True,
        )
        student_fee = StudentFee.objects.create(
            student=self.student,
            fee_definition=fee_def,
            total_amount=Decimal("5000.00"),
            paid_amount=Decimal("0.00"),
            concession_amount=Decimal("100.00"),
        )
        concession = FeeConcession.objects.create(
            student=self.student,
            fee_definition=fee_def,
            concession_type="OTHER",
            amount=Decimal("100.00"),
            approved_by=self.admin_user,
            valid_from="2026-06-01",
            valid_to="2027-03-01",
            reason="Test",
        )

        request = self.factory.put(
            "/api/fees/concession/",
            {"concession_id": concession.id, "new_amount": "7000.00"},
            format="json",
        )
        force_authenticate(request, user=self.admin_user)

        response = ApplyConcessionView.as_view()(request)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Concession cannot exceed total fee amount", str(response.data))

        student_fee.refresh_from_db()
        self.assertEqual(student_fee.concession_amount, Decimal("100.00"))

    @override_settings(RAZORPAY_WEBHOOK_SECRET="")
    def test_webhook_returns_500_when_secret_missing(self):
        request = self.factory.post(
            "/api/fees/webhook/razorpay/",
            {"event": "payment.captured"},
            format="json",
            HTTP_X_RAZORPAY_SIGNATURE="invalid",
        )
        response = RazorpayWebhookView.as_view()(request)
        self.assertEqual(response.status_code, 500)

    def test_partial_refund_is_idempotent_and_tracks_amount(self):
        fee_def = FeeDefinition.objects.create(
            academic_year="2026-2027",
            standard=self.standard,
            fee_type="Tuition",
            amount=Decimal("5000.00"),
            due_date="2027-03-01",
            is_active=True,
        )
        ledger = StudentFee.objects.create(
            student=self.student,
            fee_definition=fee_def,
            total_amount=Decimal("5000.00"),
            paid_amount=Decimal("100.00"),
            concession_amount=Decimal("0.00"),
            status="PARTIAL",
        )
        payment = FeePayment.objects.create(
            student_fee=ledger,
            amount_paid=Decimal("100.00"),
            payment_mode="RAZORPAY",
            transaction_id="TXNREFUNDTEST01",
            razorpay_order_id="order_test_1",
            razorpay_payment_id="pay_test_1",
            payment_status="SUCCESS",
        )

        payload = {
            "payload": {
                "payment": {"entity": {"id": "pay_test_1"}},
                "refund": {"entity": {"id": "rfnd_1", "amount": 2000}},
            }
        }

        view = RazorpayWebhookView()
        view.handle_payment_refunded(payload)

        payment.refresh_from_db()
        ledger.refresh_from_db()
        self.assertEqual(payment.refunded_amount, Decimal("20.00"))
        self.assertEqual(payment.payment_status, "REFUND_PENDING")
        self.assertEqual(ledger.paid_amount, Decimal("80.00"))
        self.assertEqual(FeeRefundEvent.objects.count(), 1)

        view.handle_payment_refunded(payload)

        payment.refresh_from_db()
        ledger.refresh_from_db()
        self.assertEqual(payment.refunded_amount, Decimal("20.00"))
        self.assertEqual(ledger.paid_amount, Decimal("80.00"))
        self.assertEqual(FeeRefundEvent.objects.count(), 1)
