def calculate_percentage_change(current, previous):
    if not previous or previous == 0:
        return 0 
    change = ((current - previous) / previous) * 100
    return round(change, 2)

def generate_trend_analysis(data_dict, exam_order=None):
    """
    Input: 
      data_dict: {'Term 1': 4.5, 'Term 2': 3.0}
      exam_order: Optional list of keys in specific order.
    """
    analysis = []
    
    # --- THE FIX ---
    # Old Logic: Hardcoded 'Quarterly' etc.
    # New Logic: If no order is given, trust the data dictionary's own keys.
    if exam_order is None:
        exam_order = list(data_dict.keys())
    # ----------------
    
    previous_value = None
    
    for label in exam_order:
        # Get value safely (default to 0 if missing)
        current_val = data_dict.get(label, 0)
        
        entry = {
            "exam": label,
            "value": current_val,
            "change_percentage": 0,
            "trend": "neutral"
        }

        # Calculate change logic (same as before)
        if previous_value is not None and current_val > 0 and previous_value > 0:
            change = calculate_percentage_change(current_val, previous_value)
            entry['change_percentage'] = change
            if change > 0: entry['trend'] = "increase"
            elif change < 0: entry['trend'] = "decrease"

        analysis.append(entry)
        
        # Update previous value for next iteration
        if current_val > 0:
            previous_value = current_val
        
    return analysis