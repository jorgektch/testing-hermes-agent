"""Independent, revisable commercial search sessions.

The permanent lead profile is not overwritten by exploratory comparisons.
A new temporary search starts with the permanent profile, not with older
temporary constraints. A visit remains on its original property.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from . import commercial_turn, property_filters, sales_preferences


def has_main_preferences(state: dict) -> bool:
    prefs=state.get('search_preferences') or {}
    return any((prefs.get(key) or {}).get('mode') not in (None,'unknown')
               for key in ('budget','bedrooms')) or bool(
        (state.get('property_filters') or {}).get('revision'))


def _validate_model_criteria(message: str, criteria: dict, base: dict) -> dict:
    """Accept model-provided values only when mentioned or inherited verbatim."""
    valid={'bedrooms','bedrooms_any','min_area_sqm','max_area_sqm',
           'min_bathrooms','max_bathrooms','kitchen_layout',
           'private_backyard','min_parking_spaces','max_price_usd','min_price_usd',
           'relative_price_lt','relative_price_gt'}
    if set(criteria)-valid:
        raise ValueError('Unsupported property search filter.')
    parsed=commercial_turn.extracted_criteria(message)
    saved_bedrooms=base.get('bedrooms') or {}
    saved_values={
        v for v in (saved_bedrooms.get('preferred'),
                    saved_bedrooms.get('minimum'),
                    saved_bedrooms.get('maximum')) if isinstance(v,int)
    }
    numeric_text=re.findall(r'\b\d+(?:[.,]\d+)?\b',message)
    def verified_number(value, *, allowed=()):
        return (str(value).replace('.0','') in numeric_text or
                value in allowed)
    for key,value in criteria.items():
        if key=='bedrooms':
            if (not isinstance(value,int) or not 1<=value<=20 or
                    value not in commercial_turn.counts(message) and value not in saved_values):
                raise ValueError('Bedroom filter is neither declared now nor saved in the lead profile.')
        elif key=='bedrooms_any':
            if (not isinstance(value,list) or not value or
                any(not isinstance(x,int) or
                        x not in commercial_turn.counts(message) and x not in saved_values
                        for x in value)):
                raise ValueError('Bedroom options lack buyer evidence.')
        elif key in ('max_price_usd','min_price_usd'):
            inherited=(base.get('budget') or {}).get(
                'maximum_usd' if key=='max_price_usd' else 'minimum_usd')
            if not isinstance(value,int) or not 1<=value<100_000_000:
                raise ValueError('Invalid price.')
            if value!=inherited and value!=parsed.get(key):
                raise ValueError('Price must come from buyer evidence or their saved maximum.')
        elif key in ('min_area_sqm','max_area_sqm','min_bathrooms','max_bathrooms','min_parking_spaces'):
            if not isinstance(value,(float,int)) or value<0 or value>100000:
                raise ValueError('Invalid numeric search criterion.')
            if not verified_number(value,allowed=(float(value)+0.001,float(value)-0.001)) and key not in property_filters.parse(message):
                # A model may choose to avoid passing this filter; a parser
                # fallback handles visible comparative expressions.
                raise ValueError('Numeric filter lacks explicit buyer evidence.')
        elif key in ('relative_price_lt','relative_price_gt'):
            if not isinstance(value,int) or value<=0:
                raise ValueError('Invalid relative price reference.')
        elif key=='private_backyard':
            if not isinstance(value,bool):raise ValueError('Expected a boolean backyard preference.')
            if value and not re.search(r'\b(?:garden|yard|backyard)\b',message,re.I):
                raise ValueError('Backyard requirement was not mentioned.')
        elif key=='kitchen_layout':
            if value not in ('open','closed','semi_open') or 'kitchen' not in message.lower():
                raise ValueError('Kitchen layout requires buyer evidence.')
    return criteria


def derive(state: dict, message: str, criteria: dict | None=None,
           *, source_unit: dict | None=None) -> dict:
    """Build one effective search without touching permanent preferences."""
    original=copy.deepcopy(state.get('search_preferences') or sales_preferences.empty())
    previous_filters=copy.deepcopy(state.get('property_filters') or property_filters.empty())
    # Scope was decided BEFORE any automatic preference capture this turn.
    # Recomputing it after capture mislabels the first search as temporary.
    scope=state.get('_turn_scope') or commercial_turn.scope(
        message,has_main_preferences(state))
    extracted=commercial_turn.extracted_criteria(message)
    supplied=_validate_model_criteria(message,criteria or {},original)
    combined={**extracted,**supplied}
    filters=copy.deepcopy(previous_filters)
    # Relative filters from an older session should not silently accumulate.
    for key in ('relative_price_lt','relative_price_gt','source_unit_id'):
        filters[key]=None
    if scope=='temporary':
        # Keep permanent absolute criteria and apply just the current query's
        # modifications. Supersede stale bedroom criteria for a temporary ask.
        pass
    if 'bedrooms' in combined:
        original['bedrooms']={**sales_preferences.empty()['bedrooms'],
                             'mode':'preferred','preferred':combined['bedrooms']}
    elif 'bedrooms_any' in combined:
        original['bedrooms']=sales_preferences.empty()['bedrooms']
    price=combined.get('max_price_usd')
    if price is not None:
        original['budget']={**sales_preferences.empty()['budget'],
                            'mode':'cap','maximum_usd':price}
    lower=combined.get('min_price_usd')
    if lower is not None:
        original['budget']['minimum_usd']=lower
        original['budget']['mode']='range' if original['budget'].get('maximum_usd') else 'floor'
    if original['budget'].get('minimum_usd') is not None and original['budget'].get('maximum_usd') is not None and original['budget']['minimum_usd']>original['budget']['maximum_usd']:
        raise ValueError('Minimum price exceeds maximum price.')
    refinements=property_filters.parse(message,source_unit)
    for key,value in combined.items():
        if key in property_filters.empty() and key not in ('revision','source_message_id','source_unit_id'):
            refinements[key]=value
    for key in ('_bedrooms_minimum','_bedrooms_maximum'):
        if key in refinements:
            val=refinements.pop(key)
            original['bedrooms']={**sales_preferences.empty()['bedrooms'],
                                 'mode':'minimum' if key=='_bedrooms_minimum' else 'maximum',
                                 'minimum':val if key=='_bedrooms_minimum' else None,
                                 'maximum':val if key=='_bedrooms_maximum' else None}
    if refinements:
        filters.update(refinements)
    for lo,hi in (('min_area_sqm','max_area_sqm'),('min_bathrooms','max_bathrooms')):
        if filters.get(lo) is not None and filters.get(hi) is not None and filters[lo]>filters[hi]:
            if lo in refinements and hi not in refinements:filters[hi]=None
            elif hi in refinements and lo not in refinements:filters[lo]=None
            else:raise ValueError('Contradictory search bounds.')
    return {'scope':scope,'preferences':original,'filters':filters,
            'criteria':combined,'bedrooms_any':combined.get('bedrooms_any'),
            'source_unit_id':source_unit.get('unit_id') if source_unit else None}


def save_search(state: dict, effective: dict, result: dict) -> dict:
    previous=state.get('active_search') or {}
    revision=int(previous.get('revision') or 0)+1
    record={
        'revision':revision,
        'scope':effective['scope'],
        'criteria':copy.deepcopy(effective['criteria']),
        'effective_preferences':copy.deepcopy(effective['preferences']),
        'effective_filters':copy.deepcopy(effective['filters']),
        'source_unit_id':effective['source_unit_id'],
        'source_message_id':state.get('_active_source_message_id'),
        'matching_unit_ids':[u['unit_id'] for u in result.get('matching_properties',[])],
        'created_at':datetime.now(timezone.utc).isoformat()
    }
    state['active_search']=record
    state.setdefault('search_sessions',[]).append(record)
    state['search_sessions']=state['search_sessions'][-25:]
    return record
