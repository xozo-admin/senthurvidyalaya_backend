# fees/razorpay_client.py

import razorpay
import hmac
import hashlib
from django.conf import settings
from decimal import Decimal
import logging
import json

logger = logging.getLogger(__name__)

class RazorpayClient:
    def __init__(self):
        self.key_id = settings.RAZORPAY_KEY_ID
        self.key_secret = settings.RAZORPAY_KEY_SECRET
        self.webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET
        self.enabled = settings.RAZORPAY_ENABLED
        self.test_mode = getattr(settings, 'RAZORPAY_TEST_MODE', True)
        self.webhook_url = getattr(settings, 'RAZORPAY_WEBHOOK_URL', None)

        if self.enabled and self.key_id and self.key_secret:
            self.client = razorpay.Client(auth=(self.key_id, self.key_secret))
        else:
            self.client = None
            
        # Log configuration
        logger.info(f"Razorpay initialized - Enabled: {self.enabled}, Test Mode: {self.test_mode}")
        if self.webhook_url:
            logger.info(f"Webhook URL: {self.webhook_url}")
    
    def create_order(self, amount, receipt_id, notes=None):
        """
        Create a Razorpay order
        amount: in rupees (will be converted to paise)
        """
        try:
            amount_paise = int((Decimal(str(amount)) * Decimal('100')).quantize(Decimal('1')))
            
            if not self.enabled or not self.client:
                # Dummy mode - return fake order
                logger.info(f"Creating DUMMY order: {receipt_id}")
                return {
                    'success': True,
                    'dummy_mode': True,
                    'order_data': {
                        'id': f'dummy_order_{receipt_id}',
                        'amount': amount_paise,
                        'currency': 'INR',
                        'receipt': receipt_id,
                        'status': 'created'
                    }
                }
            
            # Razorpay receipt max length is 40 chars.
            receipt_id = str(receipt_id)[:40]

            # Real mode - create actual order
            order_data = {
                'amount': amount_paise,
                'currency': 'INR',
                'receipt': receipt_id,
                'payment_capture': 1  # Auto capture
            }
            
            if notes:
                order_data['notes'] = notes
                # Add webhook URL in notes for reference
                if self.webhook_url:
                    order_data['notes']['webhook_url'] = self.webhook_url
            
            logger.info(f"Creating Razorpay order: {receipt_id} for amount: {amount}")
            order = self.client.order.create(data=order_data)
            logger.info(f"Order created successfully: {order['id']}")
            
            return {
                'success': True,
                'dummy_mode': False,
                'order_data': order
            }
            
        except razorpay.errors.BadRequestError as e:
            logger.error(f"Razorpay BadRequest: {str(e)}")
            return {
                'success': False,
                'status_code': 400,
                'error_type': 'BAD_REQUEST',
                'error': 'Invalid request parameters',
                'details': str(e)
            }
        except razorpay.errors.GatewayError as e:
            logger.error(f"Razorpay GatewayError: {str(e)}")
            return {
                'success': False,
                'error': 'Payment gateway error',
                'details': str(e)
            }
        except Exception as e:
            logger.error(f"Razorpay unexpected error: {str(e)}")
            return {
                'success': False,
                'error': 'Failed to create payment order',
                'details': str(e)
            }
    
    def verify_payment(self, order_id, payment_id, signature):
        """
        Verify Razorpay payment signature
        """
        if not self.enabled:
            if not settings.DEBUG:
                logger.error("Razorpay is disabled while DEBUG=False; rejecting payment verification")
                return {
                    'success': False,
                    'error': 'Razorpay disabled in production'
                }

            logger.info(f"DUMMY mode: Verifying payment {payment_id}")
            return {
                'success': True,
                'dummy_mode': True,
                'verified': True
            }
        
        try:
            params_dict = {
                'razorpay_order_id': order_id,
                'razorpay_payment_id': payment_id,
                'razorpay_signature': signature
            }
            
            # Verify signature
            self.client.utility.verify_payment_signature(params_dict)
            logger.info(f"Payment verified successfully: {payment_id}")
            
            return {
                'success': True,
                'dummy_mode': False,
                'verified': True
            }
            
        except razorpay.errors.SignatureVerificationError as e:
            logger.error(f"Signature verification failed: {str(e)}")
            return {
                'success': False,
                'error': 'Invalid payment signature',
                'verified': False
            }
        except Exception as e:
            logger.error(f"Verification error: {str(e)}")
            return {
                'success': False,
                'error': 'Payment verification failed',
                'verified': False
            }
    
    def fetch_payment(self, payment_id):
        """
        Fetch payment details from Razorpay
        """
        if not self.enabled or not self.client:
            return {
                'success': True,
                'dummy_mode': True,
                'payment_data': {
                    'id': payment_id,
                    'status': 'captured',
                    'amount': 0,
                    'method': 'dummy'
                }
            }
        
        try:
            payment = self.client.payment.fetch(payment_id)
            logger.info(f"Fetched payment details: {payment_id}")
            return {
                'success': True,
                'dummy_mode': False,
                'payment_data': payment
            }
        except Exception as e:
            logger.error(f"Failed to fetch payment: {str(e)}")
            return {
                'success': False,
                'error': 'Failed to fetch payment details'
            }
    
    def refund_payment(self, payment_id, amount=None, notes=None):
        """
        Process refund for a payment
        """
        if not self.enabled or not self.client:
            return {
                'success': True,
                'dummy_mode': True,
                'refund_data': {
                    'id': f'dummy_refund_{payment_id}',
                    'payment_id': payment_id,
                    'status': 'processed'
                }
            }
        
        try:
            if amount:
                amount_paise = int((Decimal(str(amount)) * Decimal('100')).quantize(Decimal('1')))
                refund = self.client.payment.refund(payment_id, amount_paise)
            else:
                refund = self.client.payment.refund(payment_id)
            
            logger.info(f"Refund processed: {refund['id']}")
            return {
                'success': True,
                'dummy_mode': False,
                'refund_data': refund
            }
        except Exception as e:
            logger.error(f"Refund failed: {str(e)}")
            return {
                'success': False,
                'error': 'Refund processing failed'
            }
    
    def verify_webhook(self, request_body, signature):
        """
        Verify Razorpay webhook signature
        """
        if not self.webhook_secret:
            logger.warning("Webhook secret not configured")
            return False

        if isinstance(request_body, str):
            request_body = request_body.encode('utf-8')
        elif isinstance(request_body, dict):
            request_body = json.dumps(request_body).encode('utf-8')

        expected_signature = hmac.new(
            key=self.webhook_secret.encode('utf-8'),
            msg=request_body,
            digestmod=hashlib.sha256
        ).hexdigest()
        
        is_valid = hmac.compare_digest(expected_signature, signature)
        logger.info(f"Webhook signature verification: {'✅ Success' if is_valid else '❌ Failed'}")
        
        return is_valid
