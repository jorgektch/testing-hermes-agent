"""Derive the existing web-app catalog from a BlackPenguin-style demo bundle.

The BP v2 bundle is deliberately illustrative, not a supported production API
schema.  This is a read-only projection: NEVER expose internal/unapproved files,
reserved units, unconfirmed facts or financing offers to the public catalog.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'shared' / 'blackpenguin'
SOURCE = DATA_DIR / 'project.json'
TARGETS = [ROOT / 'shared' / 'catalog.json', ROOT / 'backend' / 'app' / 'catalog.json']
ASSET_ROOT = ROOT / 'frontend' / 'public'


def _confirmed_feature_items(items: list[dict]) -> dict[str, object]:
    """Ignore unknown, unconfirmed and malformed facts instead of guessing."""
    result = {}
    for entry in items or []:
        if entry.get('verification_status') != 'confirmed':
            continue
        values = [entry.get(k) for k in ('value_bool', 'value_number', 'value_text')]
        present = [v for v in values if v is not None]
        if len(present) == 1:
            result[entry['feature_key']] = present[0]
    return result


def _highlights(features: dict) -> list[str]:
    result = []
    kitchen = features.get('kitchen_layout')
    if kitchen in ('open', 'closed', 'semi_open'):
        result.append({'open':'Open-plan kitchen','closed':'Closed kitchen','semi_open':'Semi-open kitchen'}[kitchen])
    if features.get('private_backyard') is True:
        result.append('Private backyard')
    if features.get('private_terrace') is True:
        result.append('Private terrace')
    if features.get('parking_spaces') is not None:
        result.append(f"{features['parking_spaces']} parking space(s)")
    if features.get('backyard_area_m2') is not None:
        result.append(f"{features['backyard_area_m2']} m² backyard")
    return result


def _approved_asset(asset: dict, sources: dict[str, dict]) -> bool:
    source = sources.get(asset.get('source_id'), {})
    if asset.get('approval_status') != 'approved' or 'simulation' not in asset.get('allowed_channels', []):
        return False
    if source.get('status') != 'ready' or source.get('sharing_intent') not in ('public','external'):
        return False
    path = source.get('url', '')
    # Allow only local paths, prevent path traversal, network URLs and URL schemes.
    if not isinstance(path, str) or not re.fullmatch(r'/demo-assets/[A-Za-z0-9_.-]+\.(?:svg|pdf)', path):
        return False
    if not (ASSET_ROOT / path.lstrip('/')).is_file():
        # Can run --allow-missing-assets for the initial generation stage.
        if not ALLOW_MISSING_ASSETS:
            return False
    return True


ALLOW_MISSING_ASSETS = False


def build(allow_missing_assets: bool = False) -> dict:
    global ALLOW_MISSING_ASSETS
    ALLOW_MISSING_ASSETS = bool(allow_missing_assets)
    bp = json.loads(SOURCE.read_text(encoding='utf-8'))
    # Assemble a read-only projection; every business entity has one source file.
    for filename, keys in (
        ('property_types.json', ('property_types',)),
        ('units.json', ('units','inventory_events')),
        ('features.json', ('feature_definitions','project_features')),
        ('assets.json', ('sources','approved_sales_assets','project_property_type_media')),
    ):
        part=json.loads((DATA_DIR/filename).read_text(encoding='utf-8'))
        assert part['company_id']==bp['company_id'] and part['project_id']==bp['project']['id']
        for key in keys:bp[key]=part[key]
    project = bp['project']
    profile = bp['profile']
    fields = profile['fields']
    assert bp['example_only'] and bp['schema_version']=='hermes-demo/project/3.0'
    assert project['company_id'] == bp['company_id']
    assert profile['final_approved'] is True and project['sales_activation_status'] == 'ready'
    assert fields['sales_authorization'] is True
    types = {p['id']:p for p in bp['property_types'] if p.get('review_status')=='confirmed' and p.get('project_id')==project['id']}
    sources = {s['id']:s for s in bp['sources']}
    assets = [a for a in bp['approved_sales_assets'] if a.get('project_id')==project['id'] and a.get('company_id')==bp['company_id'] and _approved_asset(a,sources)]
    # Public unit media is derived only from approved assets with explicit
    # coverage of that EXACT unit. A project/type-only brochure is never
    # inherited as a property-specific document.
    media_types = {'gallery_image':'svg','floor_plan':'pdf','brochure':'pdf'}
    by_unit:dict[str,dict] = {}
    for asset in assets:
        asset_type=asset['asset_type']
        if asset_type not in media_types:
            continue
        src=sources[asset['source_id']]
        url=src['url']
        if not url.endswith('.'+media_types[asset_type]):
            continue  # Never mislabel an SVG as a PDF, or vice versa.
        for coverage in asset.get('coverage',[]):
            target=coverage.get('unit_id')
            if not target:
                continue
            media=by_unit.setdefault(target,{'gallery_urls':[],'floorplan_brochure_pdf':None,'documents':[]})
            if asset_type=='gallery_image':
                if url not in media['gallery_urls']:
                    media['gallery_urls'].append(url)
            else:
                if not any(document['url']==url for document in media['documents']):
                    media['documents'].append({
                        'type':asset_type,
                        'title':asset.get('display_name') or ('Floor plan' if asset_type=='floor_plan' else 'Unit brochure'),
                        'url':url,
                        'scope':'unit',
                        'unit_id':target,
                    })
                # Legacy alias retained for clients still reading a single PDF.
                if media['floorplan_brochure_pdf'] is None:
                    media['floorplan_brochure_pdf']=url
    brochure=next((sources[a['source_id']]['url'] for a in assets if a['asset_type']=='brochure' and any(not v.get('unit_id') and not v.get('property_type_id') for v in a.get('coverage',[]))),None)
    project_features=_confirmed_feature_items(bp['project_features'])
    # Project amenities come only from approved/onboarded project profile, not unit features.
    amenities=fields.get('amenities') if profile.get('field_states_sample',{}).get('amenities',{}).get('status')=='confirmed' else []
    phase_by_id={p['id']:p['name'] for p in bp['phases']}
    properties=[]
    for u in bp['units']:
        if u.get('project_id')!=project['id'] or u.get('status')!='available' or u.get('is_publishable') is not True:
            continue
        typ=types.get(u['property_type_id'])
        if not typ or typ['currency'] != 'USD' or u.get('currency')!='USD':
            continue
        if not u.get('list_price') or not u.get('built_area_m2'):
            continue
        typed=_confirmed_feature_items(typ.get('features',[]))
        typed.update(_confirmed_feature_items(u.get('features',[])))  # Unit overrides type, never unknown -> false.
        if typ['category'] not in ('house','apartment'):
            continue
        properties.append({
            'unit_id':u['unit_code'],
            'blackpenguin_unit_id':u['id'],
            'property_type_id':typ['id'],
            'property_type_code':typ['code'],
            'property_type_description':typ.get('description'),
            'phase_id':u.get('phase_id'),
            'phase_name':phase_by_id.get(u.get('phase_id')),
            'property_model':{
                'model_name':typ['name'], 'code':typ['code'],
                'bedrooms':typ.get('bedrooms'), 'bathrooms':typ.get('bathrooms'),
                'built_area_min_m2':typ.get('built_area_min_m2'),
                'built_area_max_m2':typ.get('built_area_max_m2'),
                'lot_area_min_m2':typ.get('lot_area_min_m2'),
                'lot_area_max_m2':typ.get('lot_area_max_m2'),
                'total_units':typ.get('total_units'),
                'review_status':typ.get('review_status'),
                'inventory_updated_at':typ.get('inventory_updated_at'),
                'verified_type_features':[f for f in typ.get('features',[])
                                          if f.get('verification_status')=='confirmed'],
            },
            'floor_number':u.get('floor_number'),
            'lot_area_m2':float(u['lot_area_m2']) if u.get('lot_area_m2') is not None else None,
            'currency':u.get('currency'),
            'confirmed_features':typed,
            'publication_status':'published_for_demo_only',
            'property_type':{'house':'House','apartment':'Apartment'}[typ['category']],
            'commercial_name':typ['name'],
            'bedrooms':u.get('bedrooms') if u.get('bedrooms') is not None else typ['bedrooms'],
            'bathrooms':u.get('bathrooms') if u.get('bathrooms') is not None else typ['bathrooms'],
            'area_sqm':float(u['built_area_m2']),
            'price_usd':int(float(u['list_price'])),
            'available_from_date':u.get('available_from') or 'Immediate',
            'status':'Available',  # marketing badge is derived after unit-level inventory checks
            'highlights':_highlights(typed),
            'media':{
                **by_unit.get(u['id'],{'gallery_urls':[],'floorplan_brochure_pdf':None,'documents':[]}),
                # Relative path is portable across Vercel environments; the
                # agent converts it to a configured absolute public URL.
                'gallery_url':f"/properties/{u['unit_code']}/gallery" if by_unit.get(u['id'],{}).get('gallery_urls') else None,
            },
            'demo_inventory_last_updated_at':u.get('inventory_updated_at'),
            'inventory_is_live':False,
        })
    # Derive 'last unit' only from actual available, publishable units in the
    # complete demo inventory. Never trust a potentially stale count on type.
    available_by_type={}
    for prop in properties:available_by_type[prop['property_type_id']]=available_by_type.get(prop['property_type_id'],0)+1
    for prop in properties:
        if available_by_type[prop['property_type_id']]==1:
            prop['status']='Last Unit'
    properties.sort(key=lambda x:(x['price_usd'],x['unit_id']))
    assert len(properties)>=4, f'Expected four available demo properties; found {len(properties)}'
    assert brochure, 'Approved simulated overview brochure required'
    project_info={
        'project_id':project['id'],
        'company_id':bp['company_id'],
        'name':project['name'],
        'project_type':project.get('project_type'),
        'development_status':project.get('development_status'),
        'short_description':project.get('short_description'),
        'long_description':project.get('long_description'),
        'project_code':project.get('code'),
        'published_revision':profile.get('published_revision'),
        'phase_names':[p['name'] for p in bp['phases']],
        'inventory_summary':{'total_units':len(bp['units']),
                             'available_units':sum(u.get('status')=='available' for u in bp['units']),
                             'reserved_units':sum(u.get('status')=='reserved' for u in bp['units'])},
        'developer':project.get('developer') or 'Not specified in the source',
        'location':{
            'street_address':project['address'],
            'district':'Not specified in the source',
            'city':project['city'],
            'country':'Peru' if project.get('country') in ('PE','Peru') else project['country'],
            'timezone':project['location']['timezone'],
            'onsite_showroom':'Simulated project visit — no real showroom or appointment',
        },
        'value_proposition':fields['value_proposition'],
        'amenities':amenities,
        'general_brochure_pdf':brochure,
        'media_disclaimer':'All properties, prices, published files, advisor schedules, availability and visits are synthetic, not an actual offer. Source example uses BlackPenguin v2 proposed schema, not the production API.',
        'source_model':'BlackPenguin Project v2 PROPOSED — synthetic projection',
        'project_amenities_verified_for_demo':project_features,
    }
    # Sales Advisor cards are a projection from canonical users/assignments;
    # no schedules live on the Project or in the public catalog.
    team=json.loads((DATA_DIR/'team.json').read_text(encoding='utf-8'))
    eligible={a['user_id'] for a in team['project_user_assignments']
              if a['project_id']==project['id'] and a['is_active'] and a['sales_enabled'] and a['accepts_appointments']}
    advisors=[{'advisor_id':u['demo_advisor_id'],'name':u['display_name'],'specialty':u['specialty']}
              for u in team['users'] if u['id'] in eligible and u['is_active']]
    result={'project_info':project_info,'properties':properties,'sales_advisors':advisors}
    # Business rule: financing data exists only in BP private bundle, never in agent-facing KB.
    for target in TARGETS:
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Vercel may bundle backend/ separately; mirror the lead template as well.
    (ROOT / 'backend' / 'app' / 'lead.json').write_bytes(
        (ROOT / 'shared' / 'blackpenguin' / 'lead.json').read_bytes())
    return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--allow-missing-assets',action='store_true',help='Only for bootstrapping freshly generated asset files')
    args=parser.parse_args()
    data=build(args.allow_missing_assets)
    print(f"Published compatibility catalog: {data['project_info']['name']} / {len(data['properties'])} available residences / {len(data['sales_advisors'])} demo advisors")
    for p in data['properties']:
        print(f"  {p['unit_id']:6} {p['property_type']:9} ${p['price_usd']:>7,}  galleries={len(p['media']['gallery_urls'])}  pdf={bool(p['media']['floorplan_brochure_pdf'])}")
