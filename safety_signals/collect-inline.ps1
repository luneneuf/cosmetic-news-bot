# collect-inline.ps1
# 수집 본체 (인라인 실행용)
# - sources.json을 읽어 active=true인 소스 모두 fetch
# - 결과를 output/daily/YYYYMMDD/ 폴더에 저장
# - JSON (원본 파싱본) + Markdown (사람 검수용 다이제스트)

# 본 파일은 PowerShell 실행 정책 우회를 피하기 위해 'inline 실행 권장' 형태로 작성됨.
# 실행 방법:
#   1) 이 파일 전체 내용을 PowerShell 창에 복사 후 붙여넣기
#   2) 또는 외부에서 `& 'path'` 호출이 필요하면 서명 또는 인라인 실행
# 명시적 함수 정의는 inline 실행 가능

$ErrorActionPreference = 'Continue'

# === 설정 ===
# 우선순위: 환경변수(클라우드/CI) → $PSScriptRoot(-File 실행) → 로컬 하드코딩 경로(IEX 실행 fallback)
$ScriptDir = if ($env:SAFETY_SIGNALS_DIR) { $env:SAFETY_SIGNALS_DIR }
             elseif ($PSScriptRoot)       { $PSScriptRoot }
             else                          { 'C:\Users\laka\cosmetic-news-bot\safety_signals' }
$Today = Get-Date -Format 'yyyyMMdd'
$NowText = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$DailyDir = Join-Path (Join-Path (Join-Path $ScriptDir 'output') 'daily') $Today
if (-not (Test-Path $DailyDir)) { New-Item -ItemType Directory -Path $DailyDir -Force | Out-Null }

# === 날짜 필터 ===
# 이 날짜 이전 항목은 다이제스트·JSON에서 제외 (RSS는 출처에 따라 1년치 이상도 흘러나옴)
$StartDate = [datetime]'2026-04-01'

# 다양한 출처의 published 필드를 DateTime으로 파싱 (실패 시 $null)
function Try-ParseDate {
    param([string]$s)
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    # KCIA의 "post#NNN" 같은 fallback은 파싱 불가 → 통과시킬 별도 처리 필요
    if ($s -match '^post#\d+$') { return $null }
    try {
        return [datetime]::Parse($s, [System.Globalization.CultureInfo]::InvariantCulture)
    } catch {
        # PubMed "2026 May" 같은 month-year 형식
        $m = [regex]::Match($s, '^(\d{4})\s+([A-Za-z]+)(?:\s+(\d{1,2}))?$')
        if ($m.Success) {
            try { return [datetime]::Parse("$($m.Groups[2].Value) $(if ($m.Groups[3].Success) { $m.Groups[3].Value } else { '1' }), $($m.Groups[1].Value)", [System.Globalization.CultureInfo]::InvariantCulture) } catch { return $null }
        }
        return $null
    }
}

# === 수집 대상 (검증 통과한 10개) ===
$Sources = @(
    @{ id='gnews_kr_recall';   tier='A'; country='KR';  name='Google News KR — 식약처 화장품 회수'; type='rss'; url='https://news.google.com/rss/search?q=%EC%8B%9D%EC%95%BD%EC%B2%98+%ED%99%94%EC%9E%A5%ED%92%88+%ED%9A%8C%EC%88%98&hl=ko&gl=KR' }
    @{ id='gnews_kr_safety';   tier='A'; country='KR';  name='Google News KR — 화장품 부작용'; type='rss'; url='https://news.google.com/rss/search?q=%ED%99%94%EC%9E%A5%ED%92%88+%EB%B6%80%EC%9E%91%EC%9A%A9&hl=ko&gl=KR' }
    @{ id='gnews_en_recall';   tier='B'; country='US';  name='Google News EN — cosmetic recall'; type='rss'; url='https://news.google.com/rss/search?q=cosmetic+recall&hl=en-US&gl=US' }
    @{ id='gnews_en_fda_cos';  tier='B'; country='US';  name='Google News EN — FDA cosmetics warning'; type='rss'; url='https://news.google.com/rss/search?q=FDA+cosmetics+warning+letter&hl=en-US&gl=US' }
    @{ id='gnews_en_kbeauty';  tier='B'; country='INT'; name='Google News EN — K-beauty recall/safety'; type='rss'; url='https://news.google.com/rss/search?q=K-beauty+recall+OR+safety&hl=en-US&gl=US' }
    @{ id='gnews_uk_opss';     tier='B'; country='UK';  name='Google News UK — OPSS cosmetic'; type='rss'; url='https://news.google.com/rss/search?q=OPSS+cosmetic+OR+%22product+safety%22+cosmetic&hl=en-GB&gl=GB' }
    @{ id='uk_opss_atom';      tier='B'; country='UK';  name='GOV.UK OPSS Atom feed'; type='atom'; url='https://www.gov.uk/government/organisations/office-for-product-safety-and-standards.atom' }
    @{ id='uk_govuk_search';   tier='B'; country='UK';  name='GOV.UK Search API — OPSS'; type='json_govuk'; url='https://www.gov.uk/api/search.json?filter_organisations=office-for-product-safety-and-standards&order=-public_timestamp&count=10' }
    @{ id='pubmed_eutils';     tier='C'; country='INT'; name='PubMed — cosmetic adverse'; type='pubmed'; url='https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=%28cosmetic%5BTitle%5D%29+AND+%28adverse+OR+allergy+OR+dermatitis%29&sort=date&retmode=json&retmax=10' }
    @{ id='kcia_notice_html';  tier='A'; country='KR';  name='KCIA 공지사항 (HTML 스크래핑)'; type='html_kcia'; url='https://www.kcia.or.kr/home/notice/notice.php' }
    @{ id='kcia_edu_law_html'; tier='A'; country='KR';  name='KCIA 공지/교육 — 법령 (HTML 스크래핑)'; type='html_kcia'; url='https://www.kcia.or.kr/home/edu/edu_01.php?sse=1' }
)

$UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
$AllItems = @()

foreach ($s in $Sources) {
    Write-Host "[$($s.id)] fetching..." -ForegroundColor Cyan
    try {
        switch ($s.type) {
            { $_ -in 'rss','atom' } {
                $resp = Invoke-WebRequest -Uri $s.url -UseBasicParsing -TimeoutSec 30 -UserAgent $UA -ErrorAction Stop
                [xml]$xml = $resp.Content
                $ns = New-Object System.Xml.XmlNamespaceManager($xml.NameTable)
                $ns.AddNamespace('atom','http://www.w3.org/2005/Atom')
                $entries = $xml.SelectNodes('//item')
                if ($entries.Count -eq 0) { $entries = $xml.SelectNodes('//atom:entry', $ns) }
                foreach ($e in $entries) {
                    $title = if ($e.title -is [System.Xml.XmlElement]) { $e.title.InnerText } else { $e.title }
                    $link = if ($e.link -is [System.Xml.XmlElement]) {
                        if ($e.link.href) { $e.link.href } else { $e.link.InnerText }
                    } else { $e.link }
                    $pub = if ($e.pubDate) { $e.pubDate } elseif ($e.updated) { if ($e.updated -is [System.Xml.XmlElement]) { $e.updated.InnerText } else { $e.updated } } elseif ($e.published) { if ($e.published -is [System.Xml.XmlElement]) { $e.published.InnerText } else { $e.published } } else { '' }
                    $AllItems += [PSCustomObject]@{
                        source = $s.id; tier = $s.tier; country = $s.country
                        title = ($title -replace '\s+', ' ').Trim()
                        link = $link.Trim()
                        published = $pub
                    }
                }
            }
            'json_govuk' {
                $resp = Invoke-RestMethod -Uri $s.url -TimeoutSec 30 -UserAgent $UA
                foreach ($r in $resp.results) {
                    $AllItems += [PSCustomObject]@{
                        source = $s.id; tier = $s.tier; country = $s.country
                        title = $r.title
                        link = "https://www.gov.uk$($r.link)"
                        published = $r.public_timestamp
                    }
                }
            }
            'pubmed' {
                $search = Invoke-RestMethod -Uri $s.url -TimeoutSec 30 -UserAgent $UA
                $ids = $search.esearchresult.idlist
                if ($ids -and $ids.Count -gt 0) {
                    $sumUrl = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=$($ids -join ',')&retmode=json"
                    $sum = Invoke-RestMethod -Uri $sumUrl -TimeoutSec 30 -UserAgent $UA
                    foreach ($id in $ids) {
                        $d = $sum.result.$id
                        if ($d) {
                            $AllItems += [PSCustomObject]@{
                                source = $s.id; tier = $s.tier; country = $s.country
                                title = ($d.title -replace '\s+', ' ').Trim()
                                link = "https://pubmed.ncbi.nlm.nih.gov/$id/"
                                published = $d.pubdate
                            }
                        }
                    }
                }
            }
            'html_kcia' {
                # KCIA notice.php — 게시판 행에서 카테고리·제목·번호·날짜 추출
                # 실제 구조 (2026-05 확인):
                #   <tr>
                #     <td class="no"><p>공지</p></td>                  ← 일련번호 또는 "공지"
                #     <td class="category"><p>기타</p></td>             ← 카테고리
                #     <td class="left"><a href="?type=view&no=NNNN..." class="link">제목</a></td>
                #     <td class="attach">...</td>
                #     <td>조회수</td>
                #     <td>YYYY-MM-DD</td>                              ← 등록일
                #   </tr>
                $resp = Invoke-WebRequest -Uri $s.url -UseBasicParsing -TimeoutSec 30 -UserAgent $UA
                $html = $resp.Content
                # 행 단위로 split → 각 행에서 핵심 필드 추출
                $rowMatches = [regex]::Matches($html, '<tr[^>]*>([\s\S]*?)</tr>')
                $seen = @{}
                foreach ($row in $rowMatches) {
                    $rowHtml = $row.Groups[1].Value
                    # 제목 + 번호
                    $linkMatch = [regex]::Match($rowHtml, '<a\s+href="(\?type=view&no=(\d+)[^"]*)"\s+class="link">([^<]+)</a>')
                    if (-not $linkMatch.Success) { continue }
                    $no = $linkMatch.Groups[2].Value
                    if ($seen[$no]) { continue }
                    $seen[$no] = $true
                    $title = ($linkMatch.Groups[3].Value -replace '\s+', ' ').Trim()
                    # base URL은 소스 URL에서 query string 제거하여 동적으로 구성 (notice.php / edu_01.php 등 동일 템플릿 공유)
                    $kciaBase = $s.url -replace '\?.*$', ''
                    $href = $kciaBase + $linkMatch.Groups[1].Value
                    # 카테고리
                    $catMatch = [regex]::Match($rowHtml, '<td class="category"><p>([^<]+)</p></td>')
                    $category = if ($catMatch.Success) { $catMatch.Groups[1].Value.Trim() } else { '' }
                    # 등록일: YYYY-MM-DD 또는 YYYY.MM.DD 패턴
                    $dateMatch = [regex]::Match($rowHtml, '\b(\d{4}[-./]\d{2}[-./]\d{2})\b')
                    $pubDate = if ($dateMatch.Success) { $dateMatch.Groups[1].Value } else { "post#$no" }
                    # 공지 여부
                    $noticeMatch = [regex]::Match($rowHtml, '<td class="no"><p>([^<]+)</p></td>')
                    $isNotice = $noticeMatch.Success -and $noticeMatch.Groups[1].Value.Trim() -eq '공지'
                    $titlePrefix = if ($isNotice) { '[공지] ' } else { '' }
                    if ($category) { $titlePrefix += "[$category] " }
                    $AllItems += [PSCustomObject]@{
                        source = $s.id; tier = $s.tier; country = $s.country
                        title = $titlePrefix + $title
                        link = $href
                        published = $pubDate
                    }
                }
            }
        }
    } catch {
        Write-Host "  [$($s.id)] FAILED: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "총 수집 (필터 전): $($AllItems.Count) items" -ForegroundColor Green

# === 코스메틱 관련성 필터 ===
# GOV.UK OPSS 일반 제품안전 소스는 장난감·전자제품·자전거 등 화장품 무관 경보를 모두 흘려보냄.
# 해당 소스에 한해 제목에 화장품 관련 키워드가 있는 항목만 통과 (inclusion filter).
# 다른 소스(KCIA·식약처·gnews cosmetic 쿼리·PubMed cosmetic 등)는 이미 화장품 특화라 그대로 통과.
$CosmeticFilterSources = @('uk_opss_atom', 'uk_govuk_search')
$CosmeticKeywords = @(
    'cosmetic', 'make-up', 'makeup', 'make up', 'skincare', 'skin care', 'skin cream',
    'skin lightening', 'lotion', 'cream', 'serum', 'lipstick', 'lip balm', 'lip gloss',
    'mascara', 'foundation', 'concealer', 'sunscreen', 'spf', 'shampoo', 'conditioner',
    'perfume', 'fragrance', 'nail polish', 'nail varnish', 'hair dye', 'hair colour',
    'hair color', 'moisturis', 'moisturiz', 'cleanser', 'toner', 'deodorant', 'soap',
    'beauty', 'eyeliner', 'eyeshadow', 'eye shadow', 'balm', 'scrub', 'exfoliat',
    'hydroquinone', 'whitening', 'toothpaste', 'wipes'
)
$nonCosmetic = @()
$cosmeticKept = @()
foreach ($it in $AllItems) {
    if ($CosmeticFilterSources -contains $it.source) {
        $t = ([string]$it.title).ToLower()
        $isMatch = $false
        foreach ($kw in $CosmeticKeywords) { if ($t.Contains($kw)) { $isMatch = $true; break } }
        if ($isMatch) { $cosmeticKept += $it } else { $nonCosmetic += $it }
    } else {
        $cosmeticKept += $it
    }
}
$AllItems = $cosmeticKept
Write-Host ("코스메틱 필터 (GOV.UK 소스): 유지 {0} / 화장품 무관 제외 {1}" -f $cosmeticKept.Count, $nonCosmetic.Count) -ForegroundColor Yellow

# === 날짜 필터 ===
$rawCount = $AllItems.Count
$undated = @()
$tooOld = @()
$kept = @()
foreach ($it in $AllItems) {
    $dt = Try-ParseDate $it.published
    if (-not $dt) {
        $undated += $it
    } elseif ($dt -lt $StartDate) {
        $tooOld += $it
    } else {
        $kept += $it
    }
}
$AllItems = $kept
Write-Host ("필터 적용 (>= {0}): 유지 {1} / 제외 {2} (기간 외 {3} + 날짜 불명 {4})" -f $StartDate.ToString('yyyy-MM-dd'), $kept.Count, ($rawCount - $kept.Count), $tooOld.Count, $undated.Count) -ForegroundColor Yellow

# === 신규 항목 추출 (seen_links.json 대조) ===
# 기존 seen_links.json을 읽어 이미 본 링크와 대조 → 신규만 별도 출력
# 첫 실행(파일 없음): 모든 항목을 'seen'으로 등록하고 신규는 0건으로 처리
# (베이스라인 폭주 방지 — 첫 실행 다음날부터 실제 신규 알림)
$SeenLinksPath = Join-Path $ScriptDir 'seen_links.json'
$IsFirstRun = -not (Test-Path $SeenLinksPath)
$SeenLinks = @{}
if (-not $IsFirstRun) {
    try {
        $existing = Get-Content $SeenLinksPath -Raw -Encoding utf8 | ConvertFrom-Json
        foreach ($l in $existing.links) { $SeenLinks[$l] = $true }
    } catch {
        Write-Host "  seen_links.json 파싱 실패, 빈 상태로 시작" -ForegroundColor Yellow
    }
}

$NewItems = @()
$currentLinks = @{}
foreach ($it in $AllItems) {
    if ([string]::IsNullOrWhiteSpace($it.link)) { continue }
    $currentLinks[$it.link] = $true
    if (-not $SeenLinks[$it.link]) {
        $NewItems += $it
    }
}

if ($IsFirstRun) {
    Write-Host "  첫 실행 감지 — 모든 항목을 seen으로 등록, 신규 0건으로 처리 (베이스라인 폭주 방지)" -ForegroundColor Cyan
    $NewItems = @()
}

# 새 seen_links.json 작성 (기존 + 이번 수집의 모든 링크 union)
$allLinkArr = @($SeenLinks.Keys) + @($currentLinks.Keys) | Sort-Object -Unique
[ordered]@{
    last_updated_at = (Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz')
    total = $allLinkArr.Count
    links = $allLinkArr
} | ConvertTo-Json -Depth 3 | Out-File -FilePath $SeenLinksPath -Encoding utf8

Write-Host ("신규 항목: {0}건 (필터 통과 {1}건 중 / seen_links 총 {2}개)" -f $NewItems.Count, $AllItems.Count, $allLinkArr.Count) -ForegroundColor Magenta

# 신규만 별도 출력 (PowerShell 5.1: 빈 배열·단일 원소는 ConvertTo-Json 출력 불안정 → 명시적 가드)
$newJsonPath = Join-Path $DailyDir 'new_items.json'
$newJsonText = if ($NewItems.Count -eq 0) {
    '[]'
} else {
    ConvertTo-Json -InputObject @($NewItems) -Depth 5
}
$newJsonText | Out-File -FilePath $newJsonPath -Encoding utf8
Write-Host "New items JSON saved: $newJsonPath"

$newMdPath = Join-Path $DailyDir 'new_digest.md'
$newMd = New-Object System.Collections.ArrayList
$null = $newMd.Add("# 안전관리 시그널 — 신규 항목만")
$null = $newMd.Add("")
$null = $newMd.Add("- 수집 시각: $NowText")
$null = $newMd.Add("- 신규 항목: $($NewItems.Count)건")
$null = $newMd.Add("- 첫 실행 여부: $(if ($IsFirstRun) { '예 (베이스라인 등록만, 신규 알림 없음)' } else { '아니오' })")
$null = $newMd.Add("- 비교 기준: seen_links.json (직전 실행까지 본 링크 모음)")
$null = $newMd.Add("")
if ($NewItems.Count -eq 0) {
    $null = $newMd.Add("_신규 항목 없음._")
} else {
    $null = $newMd.Add("## 소스별 신규 건수")
    $null = $newMd.Add("")
    $null = $newMd.Add("| Source | Tier | Country | Count |")
    $null = $newMd.Add("|--------|------|---------|-------|")
    $NewItems | Group-Object source | Sort-Object Count -Descending | ForEach-Object {
        $first = $_.Group | Select-Object -First 1
        $null = $newMd.Add("| $($_.Name) | $($first.tier) | $($first.country) | $($_.Count) |")
    }
    $null = $newMd.Add("")
    $null = $newMd.Add("---")
    $null = $newMd.Add("")
    foreach ($g in ($NewItems | Group-Object source)) {
        $null = $newMd.Add("## $($g.Name) (신규 $($g.Count)건)")
        $null = $newMd.Add("")
        foreach ($i in $g.Group) {
            $title = $i.title; if ($title.Length -gt 200) { $title = $title.Substring(0,197) + '...' }
            $null = $newMd.Add("- **[$($i.published)]** $title")
            $null = $newMd.Add("  - $($i.link)")
        }
        $null = $newMd.Add("")
    }
}
$newMd | Out-File -FilePath $newMdPath -Encoding utf8
Write-Host "New items digest saved: $newMdPath"

# === JSON 저장 ===
$jsonPath = Join-Path $DailyDir 'items.json'
$AllItems | ConvertTo-Json -Depth 5 | Out-File -FilePath $jsonPath -Encoding utf8
Write-Host "JSON saved: $jsonPath"

# 필터로 빠진 항목도 별도 보관 (디버깅·재검토용)
$excludedPath = Join-Path $DailyDir 'excluded.json'
@{
    cutoff = $StartDate.ToString('yyyy-MM-dd')
    too_old_count = $tooOld.Count
    undated_count = $undated.Count
    non_cosmetic_count = $nonCosmetic.Count
    too_old = $tooOld
    undated = $undated
    non_cosmetic = $nonCosmetic
} | ConvertTo-Json -Depth 5 | Out-File -FilePath $excludedPath -Encoding utf8

# === Markdown 다이제스트 저장 ===
$mdPath = Join-Path $DailyDir 'digest.md'
$md = New-Object System.Collections.ArrayList
$null = $md.Add("# 안전관리 시그널 일일 다이제스트")
$null = $md.Add("")
$null = $md.Add("- 수집 시각: $NowText")
$null = $md.Add("- 필터: 게시일 >= $($StartDate.ToString('yyyy-MM-dd')) (이전 항목·날짜 불명 제외)")
$null = $md.Add("- 필터 후 총 항목: $($AllItems.Count) (수집 원본 $rawCount → 제외 $($rawCount - $AllItems.Count))")
$null = $md.Add("- 소스 수: $(($AllItems | Group-Object source).Count)")
$null = $md.Add("- 제외 항목 상세는 ``excluded.json`` 참조")
$null = $md.Add("")
$null = $md.Add("## 소스별 건수")
$null = $md.Add("")
$null = $md.Add("| Source | Tier | Country | Count |")
$null = $md.Add("|--------|------|---------|-------|")
$AllItems | Group-Object source | Sort-Object Count -Descending | ForEach-Object {
    $first = $_.Group | Select-Object -First 1
    $null = $md.Add("| $($_.Name) | $($first.tier) | $($first.country) | $($_.Count) |")
}
$null = $md.Add("")
$null = $md.Add("---")
$null = $md.Add("")

# 소스별 최근 10개 항목
foreach ($g in ($AllItems | Group-Object source)) {
    $null = $md.Add("## $($g.Name) (총 $($g.Count)건)")
    $null = $md.Add("")
    $items = $g.Group | Select-Object -First 10
    foreach ($i in $items) {
        $title = $i.title; if ($title.Length -gt 200) { $title = $title.Substring(0,197) + '...' }
        $null = $md.Add("- **[$($i.published)]** $title")
        $null = $md.Add("  - $($i.link)")
    }
    $null = $md.Add("")
}

$md | Out-File -FilePath $mdPath -Encoding utf8
Write-Host "Markdown saved: $mdPath"
Write-Host ""
Write-Host "=== 소스별 건수 ==="
$AllItems | Group-Object source | Sort-Object Count -Descending | Format-Table Name, Count -AutoSize
