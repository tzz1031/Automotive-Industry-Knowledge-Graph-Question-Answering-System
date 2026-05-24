class MemStore:
    """纯内存存储，不落盘，只用于流水线内部传递"""
    def __init__(self):
        self._collections = {}

    def _col(self, name):
        if name not in self._collections:
            self._collections[name] = []
        return self._collections[name]

    def find_all(self, name):          return self._col(name)
    def find(self, name, query=None):
        data = self._col(name)
        return [i for i in data if all(i.get(k) == v for k, v in (query or {}).items())]
    def find_one(self, name, query):
        return next((i for i in self._col(name)
                     if all(i.get(k) == v for k, v in (query or {}).items())), None)
    def update_one(self, name, query, update, upsert=True):
        data = self._col(name)
        patch = update.get('$set', update)
        for i, item in enumerate(data):
            if all(item.get(k) == v for k, v in query.items()):
                data[i].update(patch)
                return
        if upsert:
            data.append({**query, **patch})
    def count(self, name, query=None):
        return len(self.find(name, query))
    def drop_collection(self, name):
        self._collections[name] = []

def create_storage():
    return MemStore()

# ──────────────────── 第1步: 懂车帝爬虫 ────────────────────

class DongchediCrawler:
    def __init__(self, store):
        self.store = store
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }

    def _next_data(self, url):
        req = urllib.request.Request(url, headers=self.headers)
        try:
            res = urllib.request.urlopen(req, timeout=15)
            html = res.read().decode('utf-8', errors='ignore')
            m = re.search(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', html)
            return json.loads(m.group(1)) if m else None
        except Exception as e:
            print(f'  请求失败: {e}')
            return None

    def _page_props(self, url):
        data = self._next_data(url)
        return data.get('props', {}).get('pageProps', {}) if data else {}

    def crawl_brands_and_series(self):
        """爬取热门品牌及车系"""
        url = 'https://www.dongchedi.com/auto/library/x-x-x-x-x-x-x-x-x'
        pp = self._page_props(url)
        if not pp:
            return

        for item in pp.get('allBrands', {}).get('hot_brand', []):
            info = item.get('info', {})
            bid = str(info.get('brand_id', ''))
            if bid:
                self.store.update_one('brand', {'brand_id': bid}, {'$set': {
                    'brand_id': bid, 'brand_name': info.get('brand_name', ''),
                    'series_count': info.get('on_sale_series_count', 0),
                }})

        for s in pp.get('seriesInfo', {}).get('series', []):
            sid = str(s.get('id', ''))
            self.store.update_one('series', {'series_id': sid}, {'$set': {
                'series_id': sid, 'series_name': s.get('outter_name', ''),
                'brand_id': str(s.get('brand_id', '')), 'brand_name': s.get('brand_name', ''),
                'official_price': s.get('official_price', ''),
                'car_ids': s.get('car_ids', []),
            }})

        print(f'  品牌: {self.store.count("brand")}, 车系: {self.store.count("series")}')

    def crawl_series_models(self, max_series=None):
        """爬取每个车系的车型列表"""
        series_list = self.store.find_all('series')
        if max_series:
            series_list = series_list[:max_series]

        for idx, s in enumerate(series_list):
            sid = s['series_id']
            url = f'https://www.dongchedi.com/auto/series/{sid}'
            pp = self._page_props(url)
            if not pp:
                continue

            head = pp.get('seriesHomeHead', {})
            brand = head.get('brand_name', '')
            sname = head.get('series_name', s.get('series_name', ''))

            tabs = pp.get('carModelsData', {}).get('tab_list', [])
            for tab in tabs:
                for item in tab.get('data', []):
                    if item.get('type') == '1115':
                        info = item.get('info', {})
                        cid = str(info.get('id', ''))
                        self.store.update_one('model', {'car_id': cid}, {'$set': {
                            'car_id': cid, 'series_id': str(sid),
                            'series_name': sname, 'brand_name': brand,
                            'car_name': info.get('name', ''),
                            'price': info.get('price', ''),
                            'sale_status': info.get('sale_status', ''),
                        }})

            tm = sum(1 for m in self.store.find_all('model') if m.get('series_id') == str(sid))
            print(f'  [{idx+1}/{len(series_list)}] {sname}: {tm} 个车型')
            time.sleep(random.uniform(1, 2))

    def crawl_configs(self, max_cars=None):
        """爬取每款车的详细配置"""
        models = self.store.find_all('model')
        if max_cars:
            models = models[:max_cars]

        for idx, m in enumerate(models):
            cid = m['car_id']
            url = f'https://www.dongchedi.com/auto/params-carIds-{cid}'
            pp = self._page_props(url)
            if not pp:
                continue

            raw = pp.get('rawData', {})
            ci = (raw.get('car_info', []) or [{}])[0]
            props = raw.get('properties', [])

            # grouped config
            grouped = {'_base': {
                'car_id': str(cid), 'car_name': ci.get('car_name', ''),
                'series_name': ci.get('series_name', ''), 'brand_name': ci.get('brand_name', ''),
                'official_price': ci.get('official_price', ''), 'car_year': ci.get('car_year', ''),
                'series_type': ci.get('series_type', ''),
            }}
            current_group = ''
            for p in props:
                if p.get('type') == 0:
                    current_group = p.get('text', '')
                elif p.get('type') == 1:
                    grouped.setdefault(current_group, {})[p.get('text', '')] = ''

            info_dict = ci.get('info', {})
            vals = {k: v.get('value', '') for k, v in info_dict.items() if isinstance(v, dict)}
            flat = {}
            key_to_name = {}
            for p in props:
                if p.get('type') == 0:
                    current_group = p.get('text', '')
                elif p.get('type') == 1:
                    pkey, ptext = p.get('key', ''), p.get('text', '')
                    key_to_name[pkey] = ptext
                    val = vals.get(pkey, '')
                    grouped.setdefault(current_group, {})[ptext] = val
                    flat[ptext] = val
            flat.update({key_to_name.get(k, k): v for k, v in vals.items()})

            self.store.update_one('config', {'car_id': str(cid)}, {'$set': {
                'car_id': str(cid), 'car_name': m.get('car_name', ''),
                'brand_name': m.get('brand_name', ''),
                'grouped_config': grouped, 'flat_config': flat,
            }})
            print(f'  [{idx+1}/{len(models)}] {m.get("car_name","")}')
            time.sleep(random.uniform(1, 2))

# ──────────────────── 第2步: 数据清洗 ────────────────────

class DataCleaner:
    KEY_FIELDS = {
        'car_id', 'car_name', 'series_name', 'brand_name', 'manufacturer',
        'price_wan', 'price_official', 'price_dealer',
        'energy_type', 'energy_category',
        'motor_power_kw_num', 'motor_torque_nm_num', 'motor_type', 'motor_count',
        'battery_type', 'battery_brand', 'battery_capacity_kwh_num',
        'range_km', 'range_cltc_num', 'range_wltc_num',
        'energy_consumption_per_100km_num', 'fast_charge_power_kw_num',
        'acceleration_0_100_num', 'top_speed_num',
        'car_class', 'body_structure',
        'length_mm_num', 'width_mm_num', 'height_mm_num', 'wheelbase_mm_num',
        'seats_num', 'curb_weight_kg_num',
        'drive_type', 'front_brake_type', 'rear_brake_type', 'front_tire', 'rear_tire',
        'car_year', 'sale_status', 'series_type', 'launch_date',
    }

    FIELD_MAP = {
        '厂商': 'manufacturer', '级别': 'car_class', '能源类型': 'energy_type',
        '上市时间': 'launch_date', '最高车速(km/h)': 'top_speed',
        '官方0-100km/h加速(s)': 'acceleration_0_100',
        'NEDC纯电续航里程(km)': 'range_nedc', 'CLTC纯电续航里程(km)': 'range_cltc',
        'WLTC纯电续航里程(km)': 'range_wltc',
        '长*宽*高(mm)': 'dimensions', '长x宽x高(mm)': 'dimensions',
        '轴距(mm)': 'wheelbase_mm', '车身结构': 'body_structure',
        '座位数(个)': 'seats', '整备质量(kg)': 'curb_weight_kg',
        '电动机总功率(kW)': 'motor_power_kw', '电动机总扭矩(N·m)': 'motor_torque_nm',
        '电机类型': 'motor_type', '驱动电机数': 'motor_count',
        '电池类型': 'battery_type', '电芯品牌': 'battery_brand',
        '电池容量(kWh)': 'battery_capacity_kwh',
        '百公里耗电量(kWh/100km)': 'energy_consumption',
        '驱动方式': 'drive_type',
        '前制动器类型': 'front_brake_type', '后制动器类型': 'rear_brake_type',
        '前轮胎规格': 'front_tire', '后轮胎规格': 'rear_tire',
        '快充功率(kW)': 'fast_charge_power_kw',
    }

    FUZZY_MAP = {
        ('续航', 'CLTC'): 'range_cltc', ('续航', 'WLTC'): 'range_wltc',
        ('续航', 'NEDC'): 'range_nedc', ('百公里', '加速'): 'acceleration_0_100',
        ('最高车速',): 'top_speed', ('电动机', '功率'): 'motor_power_kw',
        ('电动机', '扭矩'): 'motor_torque_nm', ('电池容量', 'kWh'): 'battery_capacity_kwh',
        ('电池类型',): 'battery_type', ('电芯', '品牌'): 'battery_brand',
        ('百公里', '耗电'): 'energy_consumption',
        ('长', '宽', '高'): 'dimensions', ('轴距',): 'wheelbase_mm',
        ('车身结构',): 'body_structure', ('座位',): 'seats',
        ('整备质量',): 'curb_weight_kg', ('驱动方式',): 'drive_type',
    }

    def __init__(self, store):
        self.store = store
        self.num_re = re.compile(r'[\d.]+')

    def _extract_num(self, text):
        if not text: return None
        m = self.num_re.search(str(text))
        return float(m.group()) if m else None

    def _parse_dims(self, s):
        if not s: return {}
        nums = self.num_re.findall(str(s))
        if len(nums) >= 3:
            return {'length_mm': int(float(nums[0])),
                    'width_mm': int(float(nums[1])),
                    'height_mm': int(float(nums[2]))}
        return {}

    def _fuzzy_match(self, cn_key):
        for keywords, en in self.FUZZY_MAP.items():
            if all(k in cn_key for k in keywords):
                return en
        return None

    def clean(self):
        configs = self.store.find_all('config')
        self.store.drop_collection('car_structured')

        for cfg in configs:
            grouped = cfg.get('grouped_config', {})
            base = grouped.get('_base', {})
            flat = cfg.get('flat_config', {})

            cs = {
                'car_id': base.get('car_id', cfg.get('car_id', '')),
                'car_name': base.get('car_name', cfg.get('car_name', '')),
                'series_name': base.get('series_name', ''),
                'brand_name': base.get('brand_name', cfg.get('brand_name', '')),
                'price_official': base.get('official_price', ''),
                'car_year': base.get('car_year', ''),
                'series_type': base.get('series_type', ''),
            }

            for cn_key, cn_val in flat.items():
                if not cn_val: continue
                en = self.FIELD_MAP.get(cn_key) or self._fuzzy_match(cn_key)
                if en: cs[en] = str(cn_val)

            if 'dimensions' in cs:
                cs.update(self._parse_dims(cs['dimensions']))

            for fld in ['length_mm', 'width_mm', 'height_mm', 'wheelbase_mm', 'curb_weight_kg',
                         'motor_power_kw', 'motor_torque_nm', 'battery_capacity_kwh',
                         'range_cltc', 'range_wltc', 'range_nedc',
                         'acceleration_0_100', 'top_speed', 'seats',
                         'energy_consumption', 'fast_charge_power_kw']:
                if fld in cs and cs[fld]:
                    n = self._extract_num(cs[fld])
                    if n: cs[fld + '_num'] = int(n) if fld in ('seats', 'range_cltc', 'range_wltc', 'range_nedc') else n

            # range_km
            for k in ['range_cltc_num', 'range_wltc_num', 'range_nedc_num']:
                if cs.get(k):
                    cs['range_km'] = cs[k]
                    break

            # price_wan
            p = cs.get('price_official', '')
            if p:
                m = self.num_re.search(str(p))
                if m: cs['price_wan'] = float(m.group())

            # energy_category
            e = cs.get('energy_type', '')
            if '纯电' in e: cs['energy_category'] = 'BEV'
            elif '插电' in e or '插混' in e: cs['energy_category'] = 'PHEV'
            elif '增程' in e: cs['energy_category'] = 'EREV'
            elif e: cs['energy_category'] = e

            cs = {k: v for k, v in cs.items() if k in self.KEY_FIELDS}
            self.store.update_one('car_structured', {'car_id': cs.get('car_id', '')}, {'$set': cs})

        print(f'  结构化: {self.store.count("car_structured")} 条')

# ──────────────────── 第3步: 供应链补充 ────────────────────

STATIC_SUPPLY_CHAIN = {
    '比亚迪': {'battery_supplier': '弗迪电池(比亚迪子公司)', 'motor_supplier': '弗迪动力',
               'adas_chip_supplier': ['地平线Journey', '英伟达Orin'],
               'soc_chip_supplier': ['高通骁龙8295', '高通骁龙8155'],
               'lidar_supplier': ['速腾聚创', '华为'], 'smart_drive_sw_supplier': '比亚迪自研(天神之眼)',
               'chassis_supplier_supplier': '比亚迪自研', 'glass_supplier': '福耀玻璃',
               'tire_supplier': ['米其林', '马牌', '固特异', '韩泰'],
               'seat_supplier': ['李尔', '延锋'], 'paint_supplier': ['艾仕得', '立邦']},
    '蔚来': {'battery_supplier': ['宁德时代', '卫蓝新能源'], 'motor_supplier': '蔚来自研',
             'adas_chip_supplier': '英伟达Orin X', 'soc_chip_supplier': '高通骁龙8295',
             'lidar_supplier': '图达通', 'smart_drive_sw_supplier': '蔚来自研(NOP+)',
             'chassis_supplier_supplier': ['采埃孚', '博世', '大陆'],
             'glass_supplier': '福耀玻璃', 'tire_supplier': ['米其林', '马牌']},
    '小鹏汽车': {'battery_supplier': ['宁德时代', '中创新航'],
                 'motor_supplier': '小鹏自研', 'adas_chip_supplier': '英伟达Orin X',
                 'soc_chip_supplier': '高通骁龙8295', 'lidar_supplier': ['速腾聚创', '禾赛科技'],
                 'smart_drive_sw_supplier': '小鹏自研(XNGP)',
                 'chassis_supplier_supplier': ['博世', '大陆'],
                 'glass_supplier': '福耀玻璃', 'tire_supplier': ['米其林', '马牌', '固特异']},
    '理想汽车': {'battery_supplier': ['宁德时代', '欣旺达'], 'motor_supplier': ['联合汽车电子', '理想自研'],
                 'adas_chip_supplier': '英伟达Orin X', 'soc_chip_supplier': '高通骁龙8295',
                 'lidar_supplier': '禾赛科技', 'smart_drive_sw_supplier': '理想自研',
                 'chassis_supplier_supplier': ['采埃孚', '天纳克'],
                 'glass_supplier': '福耀玻璃', 'tire_supplier': ['米其林', '马牌']},
    '问界': {'battery_supplier': ['宁德时代', '弗迪电池'], 'motor_supplier': '华为自研',
             'adas_chip_supplier': '华为昇腾', 'soc_chip_supplier': '高通骁龙8295',
             'lidar_supplier': '华为', 'smart_drive_sw_supplier': '华为ADS 3.0',
             'chassis_supplier_supplier': '华为(DriveONE)',
             'glass_supplier': '福耀玻璃', 'tire_supplier': ['米其林', '马牌']},
    '极氪': {'battery_supplier': ['宁德时代', '威睿'], 'motor_supplier': ['威睿', '极氪自研'],
             'adas_chip_supplier': ['英伟达Orin X', '英伟达Thor'],
             'soc_chip_supplier': '高通骁龙8295', 'lidar_supplier': ['禾赛科技', '速腾聚创'],
             'smart_drive_sw_supplier': '极氪自研', 'chassis_supplier_supplier': '极氪自研(SEA)',
             'glass_supplier': '福耀玻璃', 'tire_supplier': ['米其林', '马牌', '倍耐力']},
    '小米汽车': {'battery_supplier': ['宁德时代', '比亚迪弗迪'], 'motor_supplier': '小米自研(超级电机V8s)',
                 'adas_chip_supplier': '英伟达Orin X', 'soc_chip_supplier': '高通骁龙8295',
                 'lidar_supplier': '禾赛科技', 'smart_drive_sw_supplier': '小米自研(Xiaomi Pilot)',
                 'chassis_supplier_supplier': ['博世', '采埃孚'],
                 'glass_supplier': '福耀玻璃', 'tire_supplier': ['米其林', '马牌', '倍耐力']},
    '零跑汽车': {'battery_supplier': ['宁德时代', '中创新航'], 'motor_supplier': '零跑自研',
                 'adas_chip_supplier': ['地平线Journey 6'], 'soc_chip_supplier': ['高通骁龙8155', '高通骁龙8295'],
                 'lidar_supplier': '速腾聚创', 'smart_drive_sw_supplier': '零跑自研(LEAP Pilot)',
                 'chassis_supplier_supplier': ['博世', '大陆'],
                 'glass_supplier': '福耀玻璃', 'tire_supplier': ['马牌', '韩泰']},
    '吉利汽车': {'battery_supplier': ['宁德时代', '威睿'], 'motor_supplier': ['威睿', '沃尔沃技术'],
                 'adas_chip_supplier': ['Mobileye', '英伟达Orin'],
                 'soc_chip_supplier': '高通骁龙8155', 'chassis_supplier_supplier': ['采埃孚', '博世'],
                 'glass_supplier': '福耀玻璃', 'tire_supplier': ['米其林', '马牌', '普利司通']},
    '长安汽车': {'battery_supplier': ['宁德时代', '弗迪电池', '蜂巢能源'],
                 'motor_supplier': ['联合汽车电子', '长安自研'],
                 'adas_chip_supplier': ['地平线Journey', '英伟达Orin'],
                 'soc_chip_supplier': ['高通骁龙8155', '高通骁龙8295'],
                 'chassis_supplier_supplier': ['博世', '大陆', '采埃孚'],
                 'glass_supplier': '福耀玻璃', 'tire_supplier': ['米其林', '马牌', '韩泰']},
    '长城汽车': {'battery_supplier': ['蜂巢能源(长城子公司)', '宁德时代'],
                 'motor_supplier': '长城自研', 'adas_chip_supplier': ['地平线Journey 5', '英伟达Orin'],
                 'soc_chip_supplier': '高通骁龙8155', 'lidar_supplier': '速腾聚创',
                 'smart_drive_sw_supplier': '长城自研(Coffee Pilot)',
                 'chassis_supplier_supplier': ['博世', '大陆'],
                 'glass_supplier': '福耀玻璃', 'tire_supplier': ['米其林', '马牌']},
}

class SupplyChainEnricher:
    def __init__(self, store):
        self.store = store

    def enrich(self):
        cars = self.store.find_all('car_structured')
        self.store.drop_collection('supply_chain_vehicle')

        for car in cars:
            cid = car.get('car_id', '')
            brand = car.get('brand_name', '')
            sc = {
                'car_id': cid, 'car_name': car.get('car_name', ''),
                'brand_name': brand,
            }

            # A. 从 car_structured 提取电芯品牌
            bb = car.get('battery_brand', '')
            if bb:
                sc['battery_cell_supplier'] = bb

            # D. 静态知识库
            static = STATIC_SUPPLY_CHAIN.get(brand, {})
            for k, v in static.items():
                if not sc.get(k):
                    sc[k] = v

            self.store.update_one('supply_chain_vehicle', {'car_id': cid}, {'$set': sc})

        print(f'  供应链: {self.store.count("supply_chain_vehicle")} 条')

# ──────────────────── 第4步: 合并为 final.json ────────────────────

def merge_to_final(store):
    cars = store.find_all('car_structured')
    sc_list = store.find_all('supply_chain_vehicle')
    sc_map = {item['car_id']: item for item in sc_list}

    merged = []
    for cs in cars:
        cid = cs['car_id']
        sc = sc_map.get(cid, {})
        item = {**cs}
        for k, v in sc.items():
            if k not in ('car_id', 'car_name', 'brand_name', '_crawl_time'):
                item[k] = v
        merged.append(item)

    with open('car_data/final.json', 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f'final.json: {len(merged)} 条, {len(merged[0]) if merged else 0} 字段')

# ──────────────────── 主流程 ────────────────────

def main():
    store = create_storage()
    print()

    print('=' * 50)
    print('第1步: 爬取懂车帝数据')
    print('=' * 50)
    crawler = DongchediCrawler(store)
    crawler.crawl_brands_and_series()
    crawler.crawl_series_models()
    crawler.crawl_configs()

    print('\n' + '=' * 50)
    print('第2步: 结构化清洗')
    print('=' * 50)
    cleaner = DataCleaner(store)
    cleaner.clean()

    print('\n' + '=' * 50)
    print('第3步: 供应链补充')
    print('=' * 50)
    enricher = SupplyChainEnricher(store)
    enricher.enrich()

    print('\n' + '=' * 50)
    print('第4步: 合并 → final.json (唯一输出)')
    print('=' * 50)
    merge_to_final(store)

    print('\n[完成] 仅输出: car_data/final.json')

if __name__ == '__main__':
    main()
