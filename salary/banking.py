# salary/banking.py
import requests
import json
import uuid
import time
import random
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
import hashlib
import hmac

RAZORPAYX_BANK_CODE = 'RAZORPAYX'


class BankTransferService:
    """
    Service for handling bank-to-bank transfers
    Uses settings.PAYMENT_GATEWAY_ENABLED to control real/dummy mode
    """
    
    def __init__(self, bank_code=RAZORPAYX_BANK_CODE):
        normalized_code = str(bank_code or RAZORPAYX_BANK_CODE).upper()
        if normalized_code != RAZORPAYX_BANK_CODE:
            raise ValueError('Only RAZORPAYX bank transfer is supported')
        self.bank_configs = {
            'RAZORPAYX': {
                'provider': 'razorpayx',
                'api_url': getattr(settings, 'RAZORPAYX_API_BASE', 'https://api.razorpay.com/v1'),
                'key_id': getattr(settings, 'RAZORPAYX_KEY_ID', ''),
                'key_secret': getattr(settings, 'RAZORPAYX_KEY_SECRET', ''),
                'source_account_number': getattr(settings, 'RAZORPAYX_SOURCE_ACCOUNT_NUMBER', ''),
                'name': 'RazorpayX'
            }
        }
        self.bank_config = self.bank_configs[RAZORPAYX_BANK_CODE]
        self.gateway_enabled = getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False)
        self.provider = 'razorpayx'

    def _build_endpoint(self, endpoint):
        """
        Build transfer endpoints safely even when configured URLs already include a version prefix.
        """
        base_url = (self.bank_config.get('api_url') or '').rstrip('/')
        if not base_url:
            return ''
        if base_url.endswith('/v1'):
            return f"{base_url}{endpoint}"
        return f"{base_url}/v1{endpoint}"

    @staticmethod
    def _safe_json(response):
        try:
            return response.json()
        except ValueError:
            return {}
        
    def initiate_transfer(self, employee_details, amount, description):
        """
        Initiate bank transfer to employee
        Returns appropriate response based on gateway mode
        """
        if not self.gateway_enabled:
            return self._dummy_transfer(employee_details, amount, description)
        else:
            return self._real_transfer(employee_details, amount, description)
    
    def _dummy_transfer(self, employee_details, amount, description):
        """
        Dummy transfer for testing - no real API calls
        """
        print(f"[DUMMY MODE] Processing transfer to {employee_details.get('name')} for amount {amount}")
        
        # Simulate processing delay
        time.sleep(1)
        
        # Generate dummy transaction details
        transaction_id = f"TXN{uuid.uuid4().hex[:12].upper()}"
        reference_number = f"REF{int(time.time())}{random.randint(1000, 9999)}"
        
        # Simulate success/failure (90% success rate for testing)
        success = random.random() < 0.9
        
        if success:
            return {
                'success': True,
                'transaction_id': transaction_id,
                'bank_reference': reference_number,
                'status': 'completed',  # In dummy mode, complete immediately
                'message': 'Transfer completed successfully (DUMMY MODE)',
                'dummy_mode': True
            }
        else:
            return {
                'success': False,
                'error': 'Insufficient funds (DUMMY MODE ERROR)',
                'status': 'failed',
                'dummy_mode': True
            }
    
    def _real_transfer(self, employee_details, amount, description):
        """
        Real transfer using bank API
        """
        return self._real_transfer_razorpayx(employee_details, amount, description)

    def _real_transfer_razorpayx(self, employee_details, amount, description):
        """
        Real transfer using RazorpayX Payout APIs.
        """
        key_id = self.bank_config.get('key_id')
        key_secret = self.bank_config.get('key_secret')
        source_account_number = self.bank_config.get('source_account_number')

        if not key_id or not key_secret:
            return {
                'success': False,
                'error': 'RazorpayX credentials are not configured',
                'status': 'failed',
                'dummy_mode': False
            }

        if not source_account_number:
            return {
                'success': False,
                'error': 'RazorpayX source account number is not configured',
                'status': 'failed',
                'dummy_mode': False
            }

        try:
            account_holder_name = employee_details.get('account_holder_name') or employee_details.get('name')
            employee_name = employee_details.get('name') or account_holder_name or 'Employee'
            reference_id = self._generate_reference_id()
            timeout = getattr(settings, 'PAYMENT_REQUEST_TIMEOUT', 30)
            auth = (key_id, key_secret)

            # 1. Create contact
            contact_payload = {
                'name': employee_name[:40],
                'type': 'employee',
                'reference_id': f"ct_{reference_id}",
                'notes': {
                    'employee_name': employee_name[:100]
                }
            }
            contact_resp = requests.post(
                self._build_endpoint('/contacts'),
                json=contact_payload,
                auth=auth,
                timeout=timeout
            )
            contact_data = self._safe_json(contact_resp)
            if contact_resp.status_code not in [200, 201]:
                error_msg = contact_data.get('error', {}).get('description') or contact_data.get('description') or 'Failed to create RazorpayX contact'
                return {'success': False, 'error': error_msg, 'status': 'failed', 'dummy_mode': False}

            contact_id = contact_data.get('id')
            if not contact_id:
                return {'success': False, 'error': 'Missing contact ID from RazorpayX', 'status': 'failed', 'dummy_mode': False}

            # 2. Create fund account
            fund_account_payload = {
                'contact_id': contact_id,
                'account_type': 'bank_account',
                'bank_account': {
                    'name': account_holder_name[:100],
                    'ifsc': employee_details.get('ifsc_code'),
                    'account_number': employee_details.get('bank_account')
                }
            }
            fund_resp = requests.post(
                self._build_endpoint('/fund_accounts'),
                json=fund_account_payload,
                auth=auth,
                timeout=timeout
            )
            fund_data = self._safe_json(fund_resp)
            if fund_resp.status_code not in [200, 201]:
                error_msg = fund_data.get('error', {}).get('description') or fund_data.get('description') or 'Failed to create RazorpayX fund account'
                return {'success': False, 'error': error_msg, 'status': 'failed', 'dummy_mode': False}

            fund_account_id = fund_data.get('id')
            if not fund_account_id:
                return {'success': False, 'error': 'Missing fund account ID from RazorpayX', 'status': 'failed', 'dummy_mode': False}

            # 3. Create payout
            payout_payload = {
                'account_number': source_account_number,
                'fund_account_id': fund_account_id,
                'amount': int((Decimal(str(amount)) * Decimal('100')).quantize(Decimal('1'))),
                'currency': 'INR',
                'mode': 'NEFT',
                'purpose': 'salary',
                'queue_if_low_balance': True,
                'reference_id': reference_id,
                'narration': description[:30],
                'notes': {
                    'employee_name': employee_name[:100],
                    'salary_reference': reference_id
                }
            }
            payout_resp = requests.post(
                self._build_endpoint('/payouts'),
                json=payout_payload,
                auth=auth,
                timeout=timeout
            )
            payout_data = self._safe_json(payout_resp)

            if payout_resp.status_code in [200, 201, 202]:
                raw_status = payout_data.get('status')
                normalized_status = self._normalize_razorpayx_status(raw_status)
                return {
                    'success': True,
                    'transaction_id': payout_data.get('id'),
                    'bank_reference': payout_data.get('utr') or payout_data.get('reference_id') or reference_id,
                    'status': normalized_status,
                    'raw_status': raw_status,
                    'message': 'RazorpayX payout initiated successfully',
                    'dummy_mode': False
                }

            error_info = payout_data.get('error', {}) if isinstance(payout_data.get('error'), dict) else {}
            error_msg = error_info.get('description') or payout_data.get('description') or 'RazorpayX payout failed'
            return {
                'success': False,
                'error': error_msg,
                'status': 'failed',
                'dummy_mode': False
            }

        except requests.exceptions.Timeout:
            return {
                'success': False,
                'error': 'RazorpayX API timeout',
                'status': 'timeout',
                'dummy_mode': False
            }
        except requests.exceptions.ConnectionError:
            return {
                'success': False,
                'error': 'RazorpayX API connection error',
                'status': 'connection_error',
                'dummy_mode': False
            }
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': str(e),
                'status': 'error',
                'dummy_mode': False
            }
    
    def check_transfer_status(self, transaction_id):
        """
        Check status of initiated transfer
        """
        if not self.gateway_enabled:
            # In dummy mode, always return completed
            return {
                'status': 'completed',
                'transaction_id': transaction_id,
                'bank_reference': f"REF{transaction_id[-8:]}",
                'completion_time': timezone.now().isoformat(),
                'dummy_mode': True
            }
        return self._check_transfer_status_razorpayx(transaction_id)

    def _check_transfer_status_razorpayx(self, transaction_id):
        key_id = self.bank_config.get('key_id')
        key_secret = self.bank_config.get('key_secret')
        if not key_id or not key_secret:
            return {'status': 'error', 'error': 'RazorpayX credentials are not configured', 'dummy_mode': False}

        try:
            response = requests.get(
                self._build_endpoint(f"/payouts/{transaction_id}"),
                auth=(key_id, key_secret),
                timeout=10
            )
            data = self._safe_json(response)
            if response.status_code != 200:
                error_msg = data.get('error', {}).get('description') if isinstance(data.get('error'), dict) else data.get('description')
                return {
                    'status': 'unknown',
                    'error': error_msg or 'Failed to fetch RazorpayX payout status',
                    'dummy_mode': False
                }

            raw_status = data.get('status')
            return {
                'status': self._normalize_razorpayx_status(raw_status),
                'raw_status': raw_status,
                'transaction_id': data.get('id'),
                'bank_reference': data.get('utr') or data.get('reference_id'),
                'completion_time': data.get('processed_at'),
                'dummy_mode': False
            }
        except Exception as e:
            return {'status': 'error', 'error': str(e), 'dummy_mode': False}

    @staticmethod
    def _normalize_razorpayx_status(status):
        status_val = (status or '').lower()
        if status_val in ['processed']:
            return 'completed'
        if status_val in ['queued', 'pending', 'processing', 'scheduled', 'initiated']:
            return 'processing'
        if status_val in ['failed', 'reversed', 'rejected', 'cancelled']:
            return 'failed'
        return 'unknown'
    
    def _generate_reference_id(self):
        """Generate unique reference ID for transaction"""
        return f"TX{int(time.time())}{uuid.uuid4().hex[:8].upper()}"
    
    def _generate_signature(self, payload):
        """Generate HMAC signature for request"""
        message = json.dumps(payload, sort_keys=True)
        return hmac.new(
            self.bank_config['api_key'].encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()


class BulkPaymentProcessor:
    """
    Process bulk payments with batch tracking
    """
    
    def __init__(self, created_by=None):
        self.batch_id = self._generate_batch_id()
        self.results = []
        self.created_by = created_by
        self.bank_code = RAZORPAYX_BANK_CODE
        
    def process_bulk_payments(self, payments, bank_code=RAZORPAYX_BANK_CODE):
        """
        Process multiple payments in batch
        """
        self.bank_code = bank_code
        bank_service = BankTransferService(bank_code)
        
        for payment in payments:
            try:
                # Get employee bank details
                employee_details = self._get_employee_bank_details(payment)
                
                # Skip if no bank details
                if not employee_details.get('bank_account') or not employee_details.get('ifsc_code'):
                    self._mark_payment_failed(payment, 'Missing bank details')
                    self.results.append({
                        'payment_id': payment.id,
                        'employee': employee_details.get('name', 'Unknown'),
                        'amount': str(payment.net_payable),
                        'result': {
                            'success': False,
                            'error': 'Missing bank details',
                            'status': 'failed'
                        }
                    })
                    continue
                
                # Initiate transfer
                result = bank_service.initiate_transfer(
                    employee_details,
                    payment.net_payable,
                    f"Salary for {payment.month}/{payment.year}"
                )
                
                # Update payment record
                if result.get('success'):
                    payment.transaction_id = result.get('transaction_id')
                    payment.bank_reference = result.get('bank_reference')
                    payment.payment_status = 'processing' if not result.get('dummy_mode') else 'processed'
                    if result.get('dummy_mode'):
                        payment.payment_date = timezone.now()
                    payment.save()
                else:
                    self._mark_payment_failed(payment, result.get('error', 'Transfer failed'))
                
                self.results.append({
                    'payment_id': payment.id,
                    'employee': employee_details.get('name', 'Unknown'),
                    'amount': str(payment.net_payable),
                    'result': result
                })
                
            except Exception as e:
                self.results.append({
                    'payment_id': payment.id,
                    'error': str(e),
                    'result': {
                        'success': False,
                        'error': str(e),
                        'status': 'exception'
                    }
                })
                self._mark_payment_failed(payment, str(e))
        
        # Save batch record
        batch = self._save_batch_record()
        
        successful = sum(1 for r in self.results if r.get('result', {}).get('success'))
        failed = len(self.results) - successful
        
        return {
            'batch_id': self.batch_id,
            'total': len(self.results),
            'successful': successful,
            'failed': failed,
            'gateway_mode': 'REAL' if getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False) else 'DUMMY',
            'results': self.results,
            'batch_record_id': batch.id if batch else None
        }
    
    def _get_employee_bank_details(self, payment):
        """Get bank details from employee record"""
        if hasattr(payment, 'staff') and payment.staff:
            employee = payment.staff
        elif hasattr(payment, 'teacher') and payment.teacher:
            employee = payment.teacher
        else:
            return {
                'name': 'Unknown',
                'bank_account': None,
                'ifsc_code': None,
                'account_holder_name': None
            }
        
        return {
            'name': getattr(employee, 'name', 'Unknown'),
            'bank_account': getattr(employee, 'bank_account_number', None),
            'ifsc_code': getattr(employee, 'ifsc_code', None),
            'account_holder_name': getattr(employee, 'account_holder_name', getattr(employee, 'name', '')),
            'bank_name': getattr(employee, 'bank_name', '')
        }
    
    def _generate_batch_id(self):
        """Generate unique batch ID"""
        return f"BATCH{int(time.time())}{uuid.uuid4().hex[:6].upper()}"
    
    def _save_batch_record(self):
        """Save batch processing record"""
        try:
            from .models import PaymentBatch
            batch = PaymentBatch.objects.create(
                batch_id=self.batch_id,
                created_by=self.created_by,
                total_payments=len(self.results),
                successful_payments=sum(1 for r in self.results if r.get('result', {}).get('success')),
                failed_payments=sum(1 for r in self.results if not r.get('result', {}).get('success')),
                total_amount=sum(Decimal(str(r.get('amount', 0))) for r in self.results),
                status='completed',
                results=self.results,
                bank_code=self.bank_code,
                gateway_mode='REAL' if getattr(settings, 'PAYMENT_GATEWAY_ENABLED', False) else 'DUMMY'
            )
            return batch
        except Exception as e:
            print(f"Failed to save batch record: {e}")
            return None

    @staticmethod
    def _mark_payment_failed(payment, reason):
        payment.payment_status = 'failed'
        payment.remarks = f"Bank transfer failed: {reason}"
        payment.save(update_fields=['payment_status', 'remarks', 'updated_at'])
