<?php
/**
 * Fetcher OMI dall'API pubblica GEOPOI Agenzia delle Entrate.
 *
 * Architettura: lazy fetch on-demand dal frontend, cache transient lunga (90 giorni
 * = un semestre OMI). Niente aggregati centrali "inventati".
 *
 * Endpoints API GEOPOI usati (host: https://www1.agenziaentrate.gov.it/servizi/geopoi_omi/):
 *   - zoneomi.php?richiesta=5                              → JSON semestri
 *   - zoneomi.php?richiesta=2&prov=XX                      → JSON comuni con CODCOM
 *   - zoneomi.php?richiesta=6&codcom=XXX&semestre=YY       → GeoJSON zone
 *   - zoneomi.php?richiesta=8&codcom=...&zo=B1&semestre=YY → JSON tipologie macro
 *   - stampaomi.php?codcom/link_zona/sem/R|C|T/0/0/0       → HTML tabella valori
 *
 * Endpoint AJAX esposti (anche a non loggati per il form pubblico):
 *   - qvi_omi_zone_comune  → input {pr, comune}        → {zone:[...]}
 *   - qvi_omi_zona_dati    → input {pr, comune, zona}  → {tipologie:[{label, stati:{...}}]}
 *   - qvi_omi_aggiorna     → admin only: svuota cache transient
 */

if (!defined('ABSPATH')) exit;

const QVI_OMI_HOST = 'https://www1.agenziaentrate.gov.it/servizi/geopoi_omi';
const QVI_OMI_REFERER = 'https://wwwt.agenziaentrate.gov.it/geopoi_omi/index.htm';
const QVI_OMI_CACHE_TTL = 90 * DAY_IN_SECONDS;

// ─────────────────────────────────────────────────────────────
// HTTP helper con throttle 200ms
// ─────────────────────────────────────────────────────────────
function qvi_omi_http_get($url, $is_json = true) {
  static $last = 0;
  $now = microtime(true);
  $diff = ($now - $last) * 1000;
  if ($diff < 200) usleep((int)((200 - $diff) * 1000));
  $last = microtime(true);

  $resp = wp_remote_get($url, [
    'timeout' => 15,
    'headers' => [
      'User-Agent' => 'qualita-vita-italia-bot/1.0 (+https://umbertobertonelli.it)',
      'Referer'    => QVI_OMI_REFERER,
      'Accept'     => $is_json ? 'application/json, */*' : 'text/html, */*',
    ],
  ]);
  if (is_wp_error($resp)) return ['error' => $resp->get_error_message()];
  $code = wp_remote_retrieve_response_code($resp);
  if ($code !== 200) return ['error' => "HTTP $code"];
  $body = wp_remote_retrieve_body($resp);
  if ($is_json) {
    $data = json_decode($body, true);
    if (!is_array($data)) return ['error' => 'JSON parse error'];
    return ['data' => $data];
  }
  return ['data' => $body];
}

function qvi_omi_parse_num($s) {
  $s = preg_replace('/[\xc2\xa0\s]+/u', '', $s);
  $s = str_replace(['.'], '', $s);
  $s = str_replace(',', '.', $s);
  return is_numeric($s) ? (float) $s : 0;
}

// ─────────────────────────────────────────────────────────────
// Step 1: ultimo semestre disponibile.
// Auto-pulizia: se il semestre OMI è cambiato dal precedente caricato,
// svuota TUTTA la cache zone-comune (i valori vecchi non sono più validi).
// ─────────────────────────────────────────────────────────────
function qvi_omi_ultimo_semestre() {
  $cached = get_transient('qvi_omi_ultimo_semestre');
  if ($cached) return $cached;
  $r = qvi_omi_http_get(QVI_OMI_HOST . '/zoneomi.php?richiesta=5');
  if (isset($r['error'])) return null;
  $sems = array_filter(array_map(function ($x) { return $x['SEMESTRE'] ?? null; }, $r['data']));
  if (empty($sems)) return null;
  rsort($sems);
  $sem = $sems[0];

  // Se il semestre OMI è cambiato dall'ultima volta, svuota la cache (auto-rollover)
  $opts = get_option('qvi_stima', []);
  if (!is_array($opts)) $opts = [];
  $sem_prev = $opts['omi_semestre_caricato'] ?? '';
  if ($sem_prev && $sem_prev !== $sem) {
    qvi_omi_purge_cache_obsoleta($sem); // rimuove transient di semestri vecchi
    $opts['omi_semestre_caricato'] = $sem;
    $opts['omi_data_aggiornamento'] = current_time('Y-m-d');
    update_option('qvi_stima', $opts);
  }

  set_transient('qvi_omi_ultimo_semestre', $sem, DAY_IN_SECONDS);
  return $sem;
}

// ─────────────────────────────────────────────────────────────
// Step 2: mappa nome → CODCOM per una provincia (cached)
// ─────────────────────────────────────────────────────────────
function qvi_omi_codcom_provincia($pr) {
  $key = 'qvi_omi_codcom_' . $pr;
  $cached = get_transient($key);
  if (is_array($cached) && !empty($cached)) return $cached;

  $r = qvi_omi_http_get(QVI_OMI_HOST . '/zoneomi.php?richiesta=2&prov=' . urlencode($pr));
  if (isset($r['error']) || !is_array($r['data'])) return [];

  $map = [];
  foreach ($r['data'] as $row) {
    $name = trim($row['DIZIONE'] ?? '');
    $code = trim($row['CODCOM'] ?? '');
    if ($name === '' || $code === '') continue;
    $map[$name] = $code;
  }
  set_transient($key, $map, QVI_OMI_CACHE_TTL);
  return $map;
}

/**
 * Alias province: l'API GEOPOI OMI usa le sigle pre-2009 (più alcune
 * particolarità sarde). Quando il match diretto sulla sigla richiesta
 * fallisce, retry sulle sigle "padre" della provincia.
 *
 *   - Province nuove 2009 → sigle pre-2009 dei capoluoghi originari
 *   - Pesaro/Urbino: OMI usa ancora la sigla storica PS (pre-1995)
 *   - Sardegna: SU non è riconosciuta; CA/OR/SS hanno comuni ex-Nuoro
 *     ancora archiviati sotto NU
 */
function qvi_omi_alias_provincia($pr) {
  static $map = [
    'BT' => ['BA', 'FG'],   // Barletta-Andria-Trani → Bari + Foggia (per Margherita di Savoia, San Ferdinando, Trinitapoli ex-FG)
    'FC' => ['FO'],         // Forlì-Cesena → Forlì (sigla pre-1992)
    'FM' => ['AP'],         // Fermo → Ascoli Piceno
    'MB' => ['MI'],         // Monza-Brianza → Milano
    'PU' => ['PS'],         // Pesaro-Urbino → Pesaro (sigla pre-1995)
    'SU' => ['CA', 'NU'],   // Sud Sardegna → Cagliari + Nuoro
    'CA' => ['NU'],         // alcuni comuni ex-Nuoro
    'OR' => ['NU'],         // alcuni comuni ex-Nuoro
    'SS' => ['NU'],         // alcuni comuni ex-Nuoro (Budoni, San Teodoro)
  ];
  return $map[$pr] ?? [];
}

function qvi_omi_codcom_da_nome($pr, $nome_comune) {
  $up = mb_strtoupper($nome_comune, 'UTF-8');

  // Tentativo 1: provincia richiesta
  $hit = qvi_omi_lookup_in_prov($pr, $up);
  if ($hit) return $hit;

  // Tentativo 2: alias province (OMI usa sigle pre-2009)
  foreach (qvi_omi_alias_provincia($pr) as $alt_pr) {
    $hit = qvi_omi_lookup_in_prov($alt_pr, $up);
    if ($hit) return $hit;
  }
  return null;
}

function qvi_omi_lookup_in_prov($pr, $nome_up) {
  $map = qvi_omi_codcom_provincia($pr);
  if (empty($map)) return null;
  // Match diretto
  if (isset($map[$nome_up])) return $map[$nome_up];
  // Match fold: API GEOPOI usa backtick come escape ASCII per apostrofo
  // (PALAZZOLO SULL`OGLIO) e accento grave finale (ALME`); separatori variano
  // (trattino vs spazio); j arcaica vs i (Bajardo / BAIARDO).
  $alt = qvi_omi_fold_comune($nome_up);
  foreach ($map as $k => $v) {
    if (qvi_omi_fold_comune($k) === $alt) return $v;
  }
  return null;
}

function qvi_omi_fold_comune($s) {
  // Rimuovi apostrofi/backtick/prime di ogni varietà
  $s = str_replace(
    ["'", "\xe2\x80\x99", "\xe2\x80\x98", "`", "\xc2\xb4", "\xca\xbc", "\xca\xbb"],
    '',
    $s
  );
  // Trattini/dash (anche unicode) → spazio: locale "Lona-Lases" vs API "LONA LASES"
  $s = preg_replace('/[\-\x{2010}-\x{2015}]+/u', ' ', $s);
  // Rimuovi diacritici (NFD → strip combining marks)
  if (class_exists('Normalizer')) {
    $nfd = Normalizer::normalize($s, Normalizer::FORM_D);
    if (is_string($nfd)) $s = preg_replace('/\p{Mn}+/u', '', $nfd);
  } else {
    // Fallback senza intl: traslittera vocali accentate comuni IT
    $s = strtr($s, [
      'À'=>'A','Á'=>'A','Â'=>'A','Ä'=>'A',
      'È'=>'E','É'=>'E','Ê'=>'E','Ë'=>'E',
      'Ì'=>'I','Í'=>'I','Î'=>'I','Ï'=>'I',
      'Ò'=>'O','Ó'=>'O','Ô'=>'O','Ö'=>'O',
      'Ù'=>'U','Ú'=>'U','Û'=>'U','Ü'=>'U',
    ]);
  }
  // Collassa whitespace (incluso NBSP)
  $s = preg_replace('/[\xc2\xa0\s]+/u', ' ', $s);
  // Toponomastica: j arcaica → i (Bajardo→Baiardo, Aiello/Ajello)
  $s = strtr(mb_strtoupper(trim($s), 'UTF-8'), ['J' => 'I']);
  return $s;
}

// ─────────────────────────────────────────────────────────────
// Step 3: zone OMI di un comune (lista codici zone)
// ─────────────────────────────────────────────────────────────
function qvi_omi_zone_comune($codcom, $semestre) {
  $r = qvi_omi_http_get(QVI_OMI_HOST . '/zoneomi.php?richiesta=6&codcom=' . urlencode($codcom) . '&semestre=' . urlencode($semestre));
  if (isset($r['error'])) return [];
  $features = $r['data']['dat']['features'] ?? $r['data']['features'] ?? [];
  $zone = [];
  foreach ($features as $f) {
    $props = $f['properties'] ?? [];
    $zo = $props['zona'] ?? null;
    if ($zo && !in_array($zo, $zone, true)) $zone[] = $zo;
  }
  sort($zone);
  return $zone;
}

/**
 * GeoJSON FeatureCollection raw delle zone OMI di un comune.
 * Cached per (codcom, semestre).
 */
function qvi_omi_zone_geojson($codcom, $semestre) {
  $key = "qvi_omi_geo_{$codcom}_{$semestre}";
  $cached = get_transient($key);
  if (is_array($cached)) return $cached;

  $r = qvi_omi_http_get(QVI_OMI_HOST . '/zoneomi.php?richiesta=6&codcom=' . urlencode($codcom) . '&semestre=' . urlencode($semestre));
  if (isset($r['error'])) return null;
  $fc = $r['data']['dat'] ?? null;
  if (!is_array($fc) || ($fc['type'] ?? '') !== 'FeatureCollection') return null;

  // Ripulisco le features: tengo solo geometry + properties.zona
  $features = [];
  foreach (($fc['features'] ?? []) as $f) {
    $zo = $f['properties']['zona'] ?? null;
    if (!$zo || empty($f['geometry'])) continue;
    $features[] = [
      'type' => 'Feature',
      'properties' => [
        'zona'   => $zo,
        'fascia' => qvi_omi_fascia_descrizione($zo),
      ],
      'geometry' => $f['geometry'],
    ];
  }
  $out = ['type' => 'FeatureCollection', 'features' => $features];
  set_transient($key, $out, QVI_OMI_CACHE_TTL);
  return $out;
}

function qvi_omi_fascia_descrizione($codice_zona) {
  $first = strtoupper(substr($codice_zona, 0, 1));
  $map = ['B' => 'Centrale', 'C' => 'Semicentrale', 'D' => 'Periferica', 'E' => 'Suburbana', 'R' => 'Rurale'];
  return $map[$first] ?? 'Zona';
}

// ─────────────────────────────────────────────────────────────
// Step 4: link_zona di una zona (necessario per stampaomi.php)
// ─────────────────────────────────────────────────────────────
function qvi_omi_link_zona($codcom, $semestre, $zona) {
  $r = qvi_omi_http_get(QVI_OMI_HOST . '/zoneomi.php?richiesta=8&codcom=' . urlencode($codcom) . '&semestre=' . urlencode($semestre) . '&zo=' . urlencode($zona));
  if (isset($r['error']) || !is_array($r['data']) || empty($r['data'])) return null;
  foreach ($r['data'] as $t) {
    $link = $t['LINK_ZONA'] ?? '';
    if ($link) return $link;
  }
  return null;
}

// ─────────────────────────────────────────────────────────────
// Step 5: parse HTML stampaomi.php → righe valori
// Ritorna: [{tipologia, stato, min, max}]
// ─────────────────────────────────────────────────────────────
function qvi_omi_fetch_valori_html($codcom, $link_zona, $semestre, $tipo_lettera) {
  $url = QVI_OMI_HOST . '/stampaomi.php?' . $codcom . '/' . $link_zona . '/' . $semestre . '/' . $tipo_lettera . '/0/0/0';
  $r = qvi_omi_http_get($url, false);
  if (isset($r['error']) || empty($r['data'])) return [];

  libxml_use_internal_errors(true);
  $doc = new DOMDocument();
  $doc->loadHTML('<?xml encoding="UTF-8"?>' . $r['data']);
  libxml_clear_errors();
  $xpath = new DOMXPath($doc);

  $clean = function ($s) {
    $s = str_replace(["\xc2\xa0", "\xe2\x80\xa0"], '', $s);
    return trim($s);
  };

  $righe = [];
  foreach ($xpath->query('//table//tr') as $tr) {
    $tds = $tr->getElementsByTagName('td');
    if ($tds->length < 4) continue;
    $tipologia = $clean($tds->item(0)->textContent);
    $stato     = strtolower($clean($tds->item(1)->textContent));
    if (!in_array($stato, ['ottimo', 'normale', 'scadente'], true)) continue;
    $min = qvi_omi_parse_num($tds->item(2)->textContent);
    $max = qvi_omi_parse_num($tds->item(3)->textContent);
    if ($min <= 0 || $max <= 0) continue;
    $righe[] = [
      'tipologia' => $tipologia,
      'stato'     => $stato,
      'min'       => (int) round($min),
      'max'       => (int) round($max),
    ];
  }
  return $righe;
}

// ─────────────────────────────────────────────────────────────
// AGGREGATOR: dati di una zona (tipologie+valori) cached lungamente
// ─────────────────────────────────────────────────────────────
function qvi_omi_zona_dati($codcom, $zona, $semestre) {
  $key = "qvi_omi_zd_{$codcom}_{$zona}_{$semestre}";
  $cached = get_transient($key);
  if (is_array($cached)) return $cached;

  $link = qvi_omi_link_zona($codcom, $semestre, $zona);
  if (!$link) {
    set_transient($key, ['tipologie' => []], DAY_IN_SECONDS); // cache breve in caso di errore
    return ['tipologie' => []];
  }

  $tipologie = []; // key=label normalized → struttura
  foreach (['R', 'C', 'T'] as $L) {
    $righe = qvi_omi_fetch_valori_html($codcom, $link, $semestre, $L);
    foreach ($righe as $r) {
      $key_t = sanitize_title($r['tipologia']);
      if (!isset($tipologie[$key_t])) {
        $tipologie[$key_t] = [
          'key'   => $key_t,
          'label' => $r['tipologia'],
          'macro' => $L,
          'stati' => [],
        ];
      }
      $tipologie[$key_t]['stati'][$r['stato']] = ['min' => $r['min'], 'max' => $r['max']];
    }
  }

  $out = ['tipologie' => array_values($tipologie), 'link_zona' => $link];
  set_transient($key, $out, QVI_OMI_CACHE_TTL);
  return $out;
}

function qvi_omi_zone_lista($codcom, $semestre) {
  $key = "qvi_omi_zl_{$codcom}_{$semestre}";
  $cached = get_transient($key);
  if (is_array($cached)) return $cached;
  $zone = qvi_omi_zone_comune($codcom, $semestre);
  $out = [];
  foreach ($zone as $z) {
    $out[] = ['codice' => $z, 'fascia' => qvi_omi_fascia_descrizione($z)];
  }
  set_transient($key, $out, QVI_OMI_CACHE_TTL);
  return $out;
}

// ═════════════════════════════════════════════════════════════
// AJAX endpoints
// ═════════════════════════════════════════════════════════════

// Ritorna le zone OMI di un comune
function qvi_omi_ajax_zone_comune() {
  $pr = isset($_REQUEST['pr']) ? strtoupper(sanitize_text_field($_REQUEST['pr'])) : '';
  $nome = isset($_REQUEST['comune']) ? sanitize_text_field(wp_unslash($_REQUEST['comune'])) : '';
  if (!$pr || !$nome) wp_send_json_error('Parametri mancanti', 400);

  $semestre = qvi_omi_ultimo_semestre();
  if (!$semestre) wp_send_json_error('API OMI non raggiungibile', 502);

  $codcom = qvi_omi_codcom_da_nome($pr, $nome);
  if (!$codcom) wp_send_json_error("Comune \"$nome\" non trovato in banca dati OMI per provincia $pr", 404);

  $zone = qvi_omi_zone_lista($codcom, $semestre);
  if (empty($zone)) wp_send_json_error("Nessuna zona OMI per $nome ($codcom)", 502);

  $geojson = qvi_omi_zone_geojson($codcom, $semestre);

  wp_send_json_success([
    'comune'   => $nome,
    'codcom'   => $codcom,
    'semestre' => $semestre,
    'zone'     => $zone,
    'geojson'  => $geojson,
  ]);
}
add_action('wp_ajax_qvi_omi_zone_comune',        'qvi_omi_ajax_zone_comune');
add_action('wp_ajax_nopriv_qvi_omi_zone_comune', 'qvi_omi_ajax_zone_comune');

// Ritorna le tipologie di una zona con valori per stato
function qvi_omi_ajax_zona_dati() {
  $pr = isset($_REQUEST['pr']) ? strtoupper(sanitize_text_field($_REQUEST['pr'])) : '';
  $nome = isset($_REQUEST['comune']) ? sanitize_text_field(wp_unslash($_REQUEST['comune'])) : '';
  $zona = isset($_REQUEST['zona']) ? sanitize_text_field($_REQUEST['zona']) : '';
  if (!$pr || !$nome || !$zona) wp_send_json_error('Parametri mancanti', 400);

  $semestre = qvi_omi_ultimo_semestre();
  if (!$semestre) wp_send_json_error('API OMI non raggiungibile', 502);

  $codcom = qvi_omi_codcom_da_nome($pr, $nome);
  if (!$codcom) wp_send_json_error("Comune $nome non trovato", 404);

  @set_time_limit(60);
  $dati = qvi_omi_zona_dati($codcom, $zona, $semestre);
  if (empty($dati['tipologie'])) wp_send_json_error("Nessun valore OMI per $nome zona $zona", 502);

  wp_send_json_success([
    'comune'    => $nome,
    'codcom'    => $codcom,
    'zona'      => $zona,
    'fascia'    => qvi_omi_fascia_descrizione($zona),
    'semestre'  => $semestre,
    'tipologie' => $dati['tipologie'],
  ]);
}
add_action('wp_ajax_qvi_omi_zona_dati',        'qvi_omi_ajax_zona_dati');
add_action('wp_ajax_nopriv_qvi_omi_zona_dati', 'qvi_omi_ajax_zona_dati');

// ─────────────────────────────────────────────────────────────
// Cleanup cache OMI:
//  - rimuove transient riferiti a semestri vecchi
//  - hard limit: max QVI_OMI_MAX_ZONE zone cached (LRU su option_id)
// ─────────────────────────────────────────────────────────────
const QVI_OMI_MAX_ZONE = 500;

function qvi_omi_purge_cache_obsoleta($semestre_corrente) {
  global $wpdb;
  $rimossi = 0;
  // I transient OMI hanno suffisso _SEMESTRE finale (es. _20252)
  $patterns = [
    '_transient_qvi_omi_zd_%',
    '_transient_qvi_omi_zl_%',
    '_transient_qvi_omi_geo_%',
  ];
  foreach ($patterns as $like) {
    $rows = $wpdb->get_col($wpdb->prepare(
      "SELECT option_name FROM {$wpdb->options} WHERE option_name LIKE %s",
      $like
    ));
    foreach ($rows as $name) {
      if (preg_match('/_(\d{5})$/', $name, $m) && $m[1] !== $semestre_corrente) {
        $key = preg_replace('/^_transient_/', '', $name);
        delete_transient($key);
        $rimossi++;
      }
    }
  }
  return $rimossi;
}

function qvi_omi_purge_cache_eccedenza() {
  global $wpdb;
  // Conta zone (non zone_lista né geojson, solo dati)
  $count = (int) $wpdb->get_var(
    "SELECT COUNT(*) FROM {$wpdb->options} WHERE option_name LIKE '_transient_qvi_omi_zd_%'"
  );
  if ($count <= QVI_OMI_MAX_ZONE) return 0;
  $da_rimuovere = $count - QVI_OMI_MAX_ZONE;
  $rows = $wpdb->get_col($wpdb->prepare(
    "SELECT option_name FROM {$wpdb->options}
     WHERE option_name LIKE '_transient_qvi_omi_zd_%'
     ORDER BY option_id ASC LIMIT %d",
    $da_rimuovere
  ));
  $rimossi = 0;
  foreach ($rows as $name) {
    $key = preg_replace('/^_transient_/', '', $name);
    delete_transient($key);
    $rimossi++;
  }
  return $rimossi;
}

function qvi_omi_cron_pulizia() {
  // Pulizia obsoleti (semestre vecchio)
  $sem = qvi_omi_ultimo_semestre();
  $obs = $sem ? qvi_omi_purge_cache_obsoleta($sem) : 0;
  // Pulizia eccedenza (LRU)
  $exc = qvi_omi_purge_cache_eccedenza();
  if (function_exists('error_log')) {
    error_log("qvi_omi_cron_pulizia: rimossi $obs obsoleti + $exc eccedenza");
  }
}
add_action('qvi_omi_cron_pulizia', 'qvi_omi_cron_pulizia');

// Schedule "weekly" (WP non lo fornisce di default)
add_filter('cron_schedules', function ($s) {
  if (!isset($s['weekly'])) {
    $s['weekly'] = ['interval' => WEEK_IN_SECONDS, 'display' => 'Settimanale (OMI cache)'];
  }
  return $s;
});

// Registra il cron settimanale
add_action('init', function () {
  if (!wp_next_scheduled('qvi_omi_cron_pulizia')) {
    wp_schedule_event(time() + DAY_IN_SECONDS, 'weekly', 'qvi_omi_cron_pulizia');
  }
});

// Hook on-write: dopo ogni set_transient qvi_omi_zd_*, controlla eccedenza
// (lazy: lo fa il cron, ma se la cache cresce molto in fretta tra cron, fail-safe immediato)
add_action('shutdown', function () {
  // Solo se è una richiesta AJAX OMI (per non rallentare le altre richieste)
  if (!wp_doing_ajax()) return;
  $action = $_REQUEST['action'] ?? '';
  if (!in_array($action, ['qvi_omi_zone_comune', 'qvi_omi_zona_dati'], true)) return;
  // Probabilistic guard: 1/20 chance per non fare query ad ogni AJAX
  if (mt_rand(1, 20) !== 1) return;
  qvi_omi_purge_cache_eccedenza();
});

// Admin: svuota cache transient OMI
function qvi_omi_ajax_aggiorna() {
  if (!current_user_can('edit_theme_options')) wp_send_json_error('Permessi insufficienti', 403);
  check_ajax_referer('qvi_omi_aggiorna', 'nonce');

  global $wpdb;
  $like1 = $wpdb->esc_like('_transient_qvi_omi_') . '%';
  $like2 = $wpdb->esc_like('_transient_timeout_qvi_omi_') . '%';
  $deleted = $wpdb->query($wpdb->prepare("DELETE FROM {$wpdb->options} WHERE option_name LIKE %s OR option_name LIKE %s", $like1, $like2));

  // Verifica connessione
  $sem = qvi_omi_ultimo_semestre();
  if (!$sem) wp_send_json_error('API OMI non raggiungibile', 502);

  $opts = get_option('qvi_stima', []);
  if (!is_array($opts)) $opts = [];
  $opts['omi_data_aggiornamento'] = current_time('Y-m-d');
  $opts['omi_semestre_caricato']  = $sem;
  update_option('qvi_stima', $opts);

  wp_send_json_success([
    'semestre'      => $sem,
    'cache_purged'  => (int) $deleted,
    'msg'           => "Cache OMI svuotata. Connessione verificata, semestre corrente $sem. I valori per ogni comune verranno scaricati on-demand al primo utilizzo.",
  ]);
}
add_action('wp_ajax_qvi_omi_aggiorna', 'qvi_omi_ajax_aggiorna');
