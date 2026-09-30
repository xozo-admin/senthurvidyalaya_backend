from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Q
from .models import InventoryItem, StockLog
from .serializers import InventoryItemSerializer
from staff.permissions import IsAdmin

# =======================================================
# 1. ADMIN: MANAGE INVENTORY (CRUD)
# =======================================================
class AdminInventoryView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        """ View items filtered by staff_type (includes logs & status) """
        s_type = request.query_params.get('staff_type')
        items = InventoryItem.objects.all()
        
        if s_type:
            items = items.filter(staff_type=s_type)
            
        serializer = InventoryItemSerializer(items, many=True)
        return Response({"status": 200, "data": serializer.data})
    def post(self, request):
        """
        Bulk Add Inventory
        Body:
        {
            "staff_type": "external_staff",
            "items": [
                { "stock_name": "Floor Cleaner", "quantity": 5 },
                { "stock_name": "Mops", "quantity": 10 }
            ]
        }
        """
        data = request.data
        staff_type = data.get('staff_type')
        items_list = data.get('items', [])

        if not staff_type or not items_list:
            return Response({"error": "staff_type and items list are required"}, 400)

        created_count = 0
        
        with transaction.atomic():
            for item in items_list:
                name = item.get('stock_name')
                qty = item.get('quantity')

                if not name or qty is None:
                    continue

                # Create the item
                InventoryItem.objects.create(
                    stock_name=name,
                    staff_type=staff_type,
                    initial_quantity=qty,
                    current_quantity=qty # Initially full
                )
                created_count += 1

        return Response({
            "status": 200, 
            "message": f"Successfully added {created_count} items to inventory."
        })

    def put(self, request):
        """ 
        Edit or Restock Item 
        Use this to add more stock (e.g., purchased 10 new mops).
        """
        item_id = request.data.get('item_id')
        new_stock = request.data.get('add_stock') # Optional: Add to existing
        
        item = get_object_or_404(InventoryItem, id=item_id)

        # Logic A: Adding Stock (Restocking)
        if new_stock:
            added_amount = int(new_stock)
            item.current_quantity += added_amount
            item.initial_quantity += added_amount # Increase capacity tracking
            item.save()
            
            # Log the restock
            StockLog.objects.create(
                item=item,
                staff=None, # Admin
                action='restocked',
                quantity_changed=added_amount
            )
            return Response({"status": 200, "message": f"Restocked by {added_amount}"})

        # Logic B: Editing Details (Name, etc.)
        serializer = InventoryItemSerializer(item, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({"status": 200, "message": "Details Updated"})
        
        return Response(serializer.errors, status=400)

    def delete(self, request):
        """ Delete Item """
        item_id = request.data.get('item_id')
        get_object_or_404(InventoryItem, id=item_id).delete()
        return Response({"status": 200, "message": "Item Deleted"})


class AdminInventoryPaginatedView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        """
        Params:
        - staff_type
        - search
        - page (default: 1)
        - page_size (default: 10, max: 100)
        """
        s_type = (request.query_params.get('staff_type') or '').strip()
        search = (request.query_params.get('search') or '').strip()

        try:
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get('page_size', 10))
        except (TypeError, ValueError):
            return Response({"error": "Invalid page or page_size"}, status=400)

        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 10
        page_size = min(page_size, 100)

        items = InventoryItem.objects.all().order_by('-last_updated', '-id')

        if s_type and s_type.lower() != 'all':
            items = items.filter(staff_type=s_type)

        if search:
            items = items.filter(
                Q(stock_name__icontains=search)
                | Q(staff_type__icontains=search)
                | Q(logs__staff__name__icontains=search)
                | Q(logs__staff__staff_id__icontains=search)
                | Q(logs__action__icontains=search)
            ).distinct()

        total = items.count()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        if page > total_pages:
            page = total_pages

        start = (page - 1) * page_size
        end = start + page_size
        page_items = items[start:end]

        serializer = InventoryItemSerializer(page_items, many=True)

        filtered_items = items
        summary = {
            "total": total,
            "good": sum(1 for i in filtered_items if i.stock_status == "Good"),
            "low": sum(1 for i in filtered_items if i.stock_status == "Low"),
            "critical": sum(1 for i in filtered_items if i.stock_status == "Critical Low"),
            "out_of_stock": sum(1 for i in filtered_items if i.stock_status == "Out of Stock"),
            "total_quantity": sum(i.current_quantity for i in filtered_items),
        }

        return Response({
            "status": 200,
            "data": serializer.data,
            "summary": summary,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_previous": page > 1
            }
        })


# =======================================================
# 2. STAFF: CONSUME INVENTORY (FIXED)
# =======================================================
# inventory/views.py

class StaffInventoryActionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """ 
        1. View Items assigned to my role.
        2. View MY recent history (so I can delete mistakes).
        """
        user = request.user
        if not hasattr(user, 'staff_profile'):
            return Response({"error": "Access Denied"}, 403)

        my_profile = user.staff_profile
        my_role = my_profile.role
        
        # Part A: The Items List
        items = InventoryItem.objects.filter(staff_type=my_role)
        items_data = []
        for i in items:
            items_data.append({
                "id": i.id,
                "stock_name": i.stock_name,
                "available": i.current_quantity,
                "status": i.stock_status 
            })

        # Part B: My Recent Logs (Last 10 actions)
        # We need this to get the 'log_id' for deleting
        my_logs = StockLog.objects.filter(staff=my_profile).order_by('-timestamp')[:10]
        logs_data = []
        for log in my_logs:
            logs_data.append({
                "log_id": log.id,
                "item_name": log.item.stock_name,
                "action": log.action,
                "time": log.timestamp,
                "description": f"You {log.action} 1 {log.item.stock_name}"
            })
            
        return Response({
            "status": 200, 
            "inventory": items_data,
            "my_recent_history": logs_data
        })

    def post(self, request):
        """ Staff Consumes Item (Used/Damaged) """
        item_id = request.data.get('item_id')
        action = request.data.get('action') 
        
        user = request.user
        if not hasattr(user, 'staff_profile'):
            return Response({"error": "Access Denied"}, 403)

        item = get_object_or_404(InventoryItem, id=item_id)

        if item.staff_type != user.staff_profile.role:
             return Response({"error": "This item is not for your department"}, 403)

        if item.current_quantity <= 0:
            return Response({"error": "Stock is 0. Cannot use."}, 400)

        with transaction.atomic():
            # Decrease Stock
            item.current_quantity -= 1
            item.save()

            # Create Log
            StockLog.objects.create(
                item=item,
                staff=user.staff_profile,
                action=action,
                quantity_changed=-1 # Negative because stock reduced
            )

        return Response({
            "status": 200, 
            "message": f"Updated. {item.stock_name} count is now {item.current_quantity}"
        })

    def delete(self, request):
        """ 
        UNDO / DELETE an Action
        Body: { "log_id": 15 }
        """
        log_id = request.data.get('log_id')
        user = request.user
        
        if not hasattr(user, 'staff_profile'):
            return Response({"error": "Access Denied"}, 403)

        # 1. Find the Log Entry
        log = get_object_or_404(StockLog, id=log_id)

        # 2. SECURITY: Check if this log belongs to the logged-in staff
        if log.staff != user.staff_profile:
            return Response({"error": "You can only delete your own updates."}, 403)

        with transaction.atomic():
            item = log.item

            # 3. REVERSE THE MATH
            # If log.quantity_changed was -1 (Used), we subtract -1 (which means Add 1)
            # Formula: Current - Change = Original
            # Ex: 4 - (-1) = 5
            item.current_quantity -= log.quantity_changed
            item.save()

            # 4. Delete the Log
            log.delete()

        return Response({
            "status": 200, 
            "message": "Action undone. Stock count restored."
        })
