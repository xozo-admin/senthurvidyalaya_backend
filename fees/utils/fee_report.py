# fees/utils/fee_report.py

from fees.models import FeeDefinition, StudentFee, FeePayment
from students.models import Student
from django.db.models import Sum, Count, Q
from decimal import Decimal
from datetime import datetime, timedelta
import csv
from django.http import HttpResponse

class FeeReportGenerator:
    """Generate various fee reports"""
    
    @staticmethod
    def generate_class_wise_collection_report(academic_year, month=None):
        """
        Generate class-wise collection report
        """
        payments = FeePayment.objects.filter(
            student_fee__fee_definition__academic_year=academic_year,
            payment_status='SUCCESS'
        )
        
        if month:
            payments = payments.filter(payment_date__month=month)
        
        report_data = {}
        
        for payment in payments.select_related(
            'student_fee__student__section__standard',
            'student_fee__fee_definition'
        ):
            class_name = payment.student_fee.student.section.standard.name
            fee_type = payment.student_fee.fee_definition.fee_type
            
            if class_name not in report_data:
                report_data[class_name] = {
                    'total_collected': Decimal('0.00'),
                    'student_count': 0,
                    'students': set(),
                    'fee_types': {}
                }
            
            if fee_type not in report_data[class_name]['fee_types']:
                report_data[class_name]['fee_types'][fee_type] = Decimal('0.00')
            
            report_data[class_name]['total_collected'] += payment.amount_paid
            report_data[class_name]['fee_types'][fee_type] += payment.amount_paid
            report_data[class_name]['students'].add(payment.student_fee.student.id)
        
        # Convert sets to counts
        for class_name in report_data:
            report_data[class_name]['student_count'] = len(report_data[class_name]['students'])
            del report_data[class_name]['students']
            report_data[class_name]['fee_types'] = dict(report_data[class_name]['fee_types'])
        
        return report_data
    
    @staticmethod
    def generate_student_wise_payment_history(student_id, academic_year=None):
        """
        Generate payment history for a student
        """
        student = Student.objects.get(student_id=student_id)
        fees = StudentFee.objects.filter(student=student)
        
        if academic_year:
            fees = fees.filter(fee_definition__academic_year=academic_year)
        
        history = []
        
        for fee in fees.select_related('fee_definition'):
            payments = fee.payments.filter(payment_status='SUCCESS').order_by('payment_date')
            
            history.append({
                'academic_year': fee.fee_definition.academic_year,
                'fee_type': fee.fee_definition.fee_type,
                'total_amount': fee.total_amount,
                'concession': fee.concession_amount,
                'paid_amount': fee.paid_amount,
                'due_amount': fee.due_amount,
                'status': fee.status,
                'due_date': fee.fee_definition.due_date,
                'payments': [
                    {
                        'date': p.payment_date,
                        'amount': p.amount_paid,
                        'mode': p.get_payment_mode_display(),
                        'transaction_id': p.transaction_id
                    }
                    for p in payments
                ]
            })
        
        return {
            'student': {
                'id': student.student_id,
                'name': student.student_name,
                'class': student.section.standard.name if student.section else None,
                'section': student.section.name if student.section else None
            },
            'history': history
        }
    
    @staticmethod
    def generate_monthly_collection_report(year):
        """
        Generate monthly collection report for the year
        """
        months = []
        monthly_data = []
        
        for month in range(1, 13):
            payments = FeePayment.objects.filter(
                payment_date__year=year,
                payment_date__month=month,
                payment_status='SUCCESS'
            )
            
            total = payments.aggregate(Sum('amount_paid'))['amount_paid__sum'] or 0
            count = payments.count()
            
            months.append(datetime(year, month, 1).strftime('%B'))
            monthly_data.append({
                'month': month,
                'month_name': datetime(year, month, 1).strftime('%B'),
                'total': total,
                'count': count,
                'by_mode': dict(payments.values_list('payment_mode').annotate(total=Sum('amount_paid')))
            })
        
        return {
            'year': year,
            'months': months,
            'data': monthly_data,
            'total_year': sum(d['total'] for d in monthly_data)
        }
    
    @staticmethod
    def export_to_csv(report_data, filename):
        """
        Export report data to CSV
        """
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        writer = csv.writer(response)
        
        if 'student' in report_data:
            # Student-wise report
            writer.writerow(['Student ID', 'Student Name', 'Class', 'Section'])
            student = report_data['student']
            writer.writerow([student['id'], student['name'], student['class'], student['section']])
            writer.writerow([])
            writer.writerow(['Academic Year', 'Fee Type', 'Total', 'Paid', 'Due', 'Status'])
            
            for item in report_data['history']:
                writer.writerow([
                    item['academic_year'],
                    item['fee_type'],
                    item['total_amount'],
                    item['paid_amount'],
                    item['due_amount'],
                    item['status']
                ])
        
        elif 'year' in report_data:
            # Monthly report
            writer.writerow(['Month', 'Total Collection', 'Transactions'])
            for item in report_data['data']:
                writer.writerow([item['month_name'], item['total'], item['count']])
            writer.writerow([])
            writer.writerow(['Year Total:', report_data['total_year']])
        
        return response