"""
The search-term vocabulary, as DATA.

The skill this replaced hardcoded one seller's regexes inside its processing
script; retiring that coupling is the whole point of this module. Patterns live
here, keyed by lexicon key, and every ProductGroup names the key it uses.

Bump LEXICON_VERSION whenever a pattern changes — SearchTermTag rows carry the
version they were classified under, so a bump reclassifies stale rows only.

Language note (verification V3, partially answered from the dev snapshot):
the AE and SA head terms are English ("towel", "bath towel", "face towel"),
so English patterns carry the money. Arabic seeds are included for the long
tail; without them Arabic searches would fall to `non_towel` and be
mis-recommended as negative keywords, which is the specific failure this
guards against.
"""
import re

LEXICON_VERSION = 1


def _rx(*parts: str) -> re.Pattern:
    return re.compile('|'.join(parts), re.I)


# ── Product types ────────────────────────────────────────────────────────────
# Order matters: the first match wins, so specific types precede generic ones.
# `non_towel` sits last among positives and catches the off-category traps that
# eat budget (paper towels, towel racks) — those are the negative-keyword pool.
PRODUCT_TYPES_TOWEL = [
    # Off-category FIRST: "paper towels" must never read as a towel we sell.
    ('non_towel',      _rx(r'\bpaper\s+towel', r'\bshop\s+towel', r'\btowel\s+(rack|bar|holder|warmer|ring|hook|rail|stand)',
                           r'\bpet\s+towel', r'\bcar\s+(wash|drying)\s+towel', r'\bgolf\s+towel',
                           r'\bnapkin', r'\bdiaper', r'\btoilet\s+paper', r'\bcleaning\s+(rag|wipe)')),
    ('bath_sheet',     _rx(r'\bbath\s+sheet')),
    ('beach_towel',    _rx(r'\bbeach\s+towel', r'\bpool\s+towel', r'\bswim\s+towel', r'\bcabana')),
    ('bath_mat',       _rx(r'\bbath\s*(room)?\s*(mat|rug)', r'\bbath\s+mat')),
    ('washcloth',      _rx(r'\bwash\s*cloth', r'\bwash\s*clothes?\b', r'\bwashrag', r'\bface\s+cloth',
                           r'\bflannel\b')),
    ('face_towel',     _rx(r'\bface\s+towel', r'\bfacial\s+towel')),
    ('kitchen_towel',  _rx(r'\bkitchen\s+(hand\s+)?towel', r'\bdish\s+(towel|cloth|rag)', r'\btea\s+towel',
                           r'\bdish\s+drying', r'\bkitchen\s+cloth')),
    ('hand_towel',     _rx(r'\bhand\s+towel', r'\bguest\s+towel', r'\bfingertip\s+towel')),
    ('bath_towel',     _rx(r'\bbath\s+towel', r'\btowels?\s+for\s+(the\s+)?bath(room)?', r'\bbathroom\s+towel',
                           r'\bbody\s+towel', r'\bshower\s+towel')),
    ('mattress_protector', _rx(r'\bmattress\s+(protector|pad|cover|topper)')),
    ('bedsheet',       _rx(r'\bbed\s*sheet', r'\bfitted\s+sheet', r'\bflat\s+sheet', r'\bsheet\s+set',
                           r'\bduvet', r'\bcomforter', r'\bquilt\b')),
    ('pillowcase',     _rx(r'\bpillow\s*(case|cover|sham)', r'\bpillow\b')),
    # Generic head terms — real demand, no product signal. Must come last.
    ('generic_towel',  _rx(r'\btowels?\b', r'\bmanashif', r'مناشف', r'منشفة')),
]

PRODUCT_TYPE_LABELS = {
    'bath_towel':   'Bath Towels',
    'hand_towel':   'Hand Towels',
    'washcloth':    'Wash Cloths',
    'kitchen_towel': 'Kitchen Towels',
    'bath_sheet':   'Bath Sheets',
    'beach_towel':  'Beach Towels',
    'face_towel':   'Face Towels',
    'bath_mat':     'Bath Mats',
    'bedsheet':     'Bed Linen',
    'pillowcase':   'Pillow Cases',
    'mattress_protector': 'Mattress Protectors',
    'generic_towel': 'Towels (generic)',
    'non_towel':    'Off-category',
    'unknown':      'Unclassified',
}


# ── Attributes ───────────────────────────────────────────────────────────────
# Namespaced `family:value`. These are what turn a head term into a demand node
# ("bath_towel" + "color:white" + "pack:4"), so the tree in v2 §6 is built from
# exactly these tags.
ATTRIBUTES = [
    ('color:white',      _rx(r'\bwhite\b', r'\bivory\b')),
    ('color:grey',       _rx(r'\bgr[ea]y\b', r'\bcharcoal\b')),
    ('color:black',      _rx(r'\bblack\b')),
    ('color:beige',      _rx(r'\bbeige\b', r'\btaupe\b', r'\bcream\b', r'\bsand\b')),
    ('color:blue',       _rx(r'\bblue\b', r'\bnavy\b', r'\bteal\b', r'\baqua\b')),
    ('color:multi',      _rx(r'\bmulti\s*colou?r', r'\bassorted\b', r'\bmixed\s+colou?r')),

    ('material:cotton',  _rx(r'\bcotton\b', r'\bterry\b', r'\bturkish\b', r'\begyptian\b', r'\bcombed\b')),
    ('material:bamboo',  _rx(r'\bbamboo\b')),
    ('material:microfiber', _rx(r'\bmicro\s*fib(er|re)\b')),
    ('material:linen',   _rx(r'\blinen\b', r'\bwaffle\b')),

    ('size:extra_large', _rx(r'\bextra\s+large\b', r'\boversized\b', r'\bjumbo\b', r'\bxl\b',
                             r'\b(3[0-9]|4[0-9])\s*x\s*(5[0-9]|6[0-9]|7[0-9])\b')),
    ('size:large',       _rx(r'\blarge\b', r'\bbig\b')),
    ('size:small',       _rx(r'\bsmall\b', r'\bcompact\b')),

    ('pack:2',           _rx(r'\b(set\s+of\s+2|2\s*[- ]?pack|pack\s+of\s+2|two\s+pack)\b')),
    ('pack:4',           _rx(r'\b(set\s+of\s+4|4\s*[- ]?pack|pack\s+of\s+4|four\s+pack)\b')),
    ('pack:6',           _rx(r'\b(set\s+of\s+6|6\s*[- ]?pack|pack\s+of\s+6|six\s+pack)\b')),
    ('pack:8',           _rx(r'\b(set\s+of\s+8|8\s*[- ]?pack|pack\s+of\s+8|eight\s+pack)\b')),
    ('pack:12',          _rx(r'\b(set\s+of\s+12|12\s*[- ]?pack|pack\s+of\s+12|dozen)\b')),
    ('pack:set',         _rx(r'\bsets?\b', r'\bbundle\b')),

    ('quality:luxury',   _rx(r'\bluxur(y|ious)\b', r'\bpremium\b', r'\bdeluxe\b', r'\bspa\s+quality\b',
                             r'\bhigh\s+end\b')),
    ('quality:cheap',    _rx(r'\bcheap\b', r'\bbudget\b', r'\bclearance\b', r'\bdiscount\b', r'\bbulk\b')),
    ('feel:soft',        _rx(r'\bsoft\b', r'\bfluffy\b', r'\bplush\b', r'\bcosy\b', r'\bcozy\b')),
    ('feel:absorbent',   _rx(r'\babsorb', r'\bhighly\s+absorbent\b')),
    ('feature:quick_dry', _rx(r'\bquick\s*dry', r'\bfast\s*dry', r'\blightweight\b')),
    ('feature:gsm',      _rx(r'\b\d{3,4}\s*gsm\b', r'\bgsm\b')),
    ('feature:organic',  _rx(r'\borganic\b', r'\beco\b', r'\bsustainable\b')),
    ('feature:decorative', _rx(r'\bdecorative\b', r'\bembroidered\b', r'\bpatterned\b', r'\bstriped\b')),
]

ATTRIBUTE_LABELS = {
    'color:white': 'White', 'color:grey': 'Grey', 'color:black': 'Black',
    'color:beige': 'Beige', 'color:blue': 'Blue', 'color:multi': 'Multicolour',
    'material:cotton': 'Cotton', 'material:bamboo': 'Bamboo',
    'material:microfiber': 'Microfibre', 'material:linen': 'Linen/Waffle',
    'size:extra_large': 'Extra Large', 'size:large': 'Large', 'size:small': 'Small',
    'pack:2': '2-Pack', 'pack:4': '4-Pack', 'pack:6': '6-Pack',
    'pack:8': '8-Pack', 'pack:12': '12-Pack', 'pack:set': 'Set',
    'quality:luxury': 'Luxury', 'quality:cheap': 'Budget',
    'feel:soft': 'Soft', 'feel:absorbent': 'Absorbent',
    'feature:quick_dry': 'Quick Dry', 'feature:gsm': 'GSM stated',
    'feature:organic': 'Organic', 'feature:decorative': 'Decorative',
}


# ── Room / usage ─────────────────────────────────────────────────────────────
ROOM_USAGE = [
    ('hotel',    _rx(r'\bhotel\b', r'\bresort\b')),
    ('spa',      _rx(r'\bspa\b', r'\bsalon\b', r'\bmassage\b', r'\bbarber\b')),
    ('gym',      _rx(r'\bgym\b', r'\bworkout\b', r'\bfitness\b', r'\bsport\b', r'\byoga\b')),
    ('baby',     _rx(r'\bbaby\b', r'\binfant\b', r'\bnewborn\b', r'\bkids?\b', r'\bchildren\b')),
    ('kitchen',  _rx(r'\bkitchen\b', r'\bdish', r'\bcooking\b')),
    ('bathroom', _rx(r'\bbath\s*room\b', r'\bbath\b', r'\bshower\b', r'\bguest\s+bath')),
    ('beach',    _rx(r'\bbeach\b', r'\bpool\b', r'\bswim')),
    ('camping',  _rx(r'\bcamping\b', r'\btravel\b', r'\bbackpack')),
]


# ── Brand classification ─────────────────────────────────────────────────────
OUR_BRAND = _rx(r'\binfinitee\b', r'\binfinity\s+xclusive', r'\binfinitee\s*x', r'\bxclusives?\b')

# Home-textile brands that appear in this category's search terms. Additive:
# a name here only changes how a term is CLASSIFIED, never whether we bid.
COMPETITOR_BRAND = _rx(
    r'\bamazon\s*basics\b', r'\bamazonbasics\b', r'\butopia\b', r'\bawa[sk]ino\b',
    r'\bcotton\s*paradise\b', r'\bchakir\b', r'\bwhite\s*classic\b', r'\bmagshion\b',
    r'\bcomfy\b', r'\bhammam\b', r'\bhomelover\b', r'\bhomelabels\b',
    r'\bmartha\s+stewart\b', r'\bwamsutta\b', r'\bcharisma\b', r'\bthreshold\b',
    r'\bribbed\s*home\b', r'\bcannon\b', r'\bfieldcrest\b', r'\bspringmaid\b',
    r'\bjcpenney\b', r'\bikea\b', r'\bjohn\s+lewis\b', r'\bdunelm\b', r'\bnext\b',
    r'\bchristy\b', r'\bsilentnight\b', r'\bsnuggledown\b', r'\bbrooklinen\b',
)

ASIN_RE = re.compile(r'^b0[a-z0-9]{8}$', re.I)


LEXICONS = {
    'towel': {
        'product_types': PRODUCT_TYPES_TOWEL,
        'attributes':    ATTRIBUTES,
        'room_usage':    ROOM_USAGE,
        'our_brand':     OUR_BRAND,
        'competitor':    COMPETITOR_BRAND,
    },
}


def get_lexicon(key: str) -> dict:
    """Return the named lexicon, falling back to 'towel' (the only one today)."""
    return LEXICONS.get(key) or LEXICONS['towel']
