# verify-sources.ps1
# 안전관리 시그널 수집 후보 출처의 동작 여부를 검증
# 사용: pwsh ./verify-sources.ps1
# 출력: output/verify_results_YYYYMMDD_HHmm.md + output/verify_results_latest.md

$ErrorActionPreference = 'Continue'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourcesFile = Join-Path $ScriptDir 'sources.json'
$OutputDir = Join-Path $ScriptDir 'output'
$Timestamp = Get-Date -Format 'yyyyMMdd_HHmm'
$OutputFile = Join-Path $OutputDir "verify_results_$Timestamp.md"
$LatestFile = Join-Path $OutputDir 'verify_results_latest.md'

if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir | Out-Null }

$config = Get-Content $SourcesFile -Raw -Encoding utf8 | ConvertFrom-Json

$report = New-Object System.Collections.ArrayList
$null = $report.Add("# 안전관리 시그널 출처 검증 결과")
$null = $report.Add("")
$null = $report.Add("- 실행 시각: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$null = $report.Add("- 검증 후보: $($config.candidates.Count)개")
$null = $report.Add("- 각 후보의 모든 URL을 순차 시도하여 첫 번째로 성공한 URL만 표시")
$null = $report.Add("")

$summary = New-Object System.Collections.ArrayList

function Test-RssOrXml {
    param([string]$Content)
    try {
        [xml]$x = $Content
        $items = $x.SelectNodes('//item')
        if ($items.Count -eq 0) { $items = $x.SelectNodes('//entry') }
        return @{ ok = $true; itemCount = $items.Count; rootName = $x.DocumentElement.LocalName }
    } catch {
        return @{ ok = $false; error = $_.Exception.Message }
    }
}

foreach ($cand in $config.candidates) {
    $null = $report.Add("## $($cand.id) — $($cand.name)")
    $null = $report.Add("")
    $null = $report.Add("- **Tier**: $($cand.tier) / **Country**: $($cand.country) / **Type**: $($cand.type)")
    if ($cand.search_term) { $null = $report.Add("- **검색식**: ``$($cand.search_term)``") }
    $null = $report.Add("")

    $candSuccess = $false
    $candWorkingUrl = $null
    $candItemCount = 0
    $candNote = ""

    foreach ($url in $cand.urls) {
        $null = $report.Add("### 시도 URL: $url")
        $null = $report.Add("")

        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15 -MaximumRedirection 5 -ErrorAction Stop
            $status = $resp.StatusCode
            $contentType = $resp.Headers.'Content-Type'
            $length = $resp.RawContentLength
            if (-not $length) { $length = $resp.Content.Length }

            $null = $report.Add("- HTTP 상태: ``$status``")
            $null = $report.Add("- Content-Type: ``$contentType``")
            $null = $report.Add("- 응답 길이: $length bytes")

            # RSS/XML 시도
            $xmlResult = Test-RssOrXml -Content $resp.Content
            if ($xmlResult.ok) {
                $null = $report.Add("- **XML 파싱 성공** — 루트: ``$($xmlResult.rootName)``, 아이템: **$($xmlResult.itemCount)개**")
                if ($xmlResult.itemCount -gt 0) {
                    $null = $report.Add("- ✅ **검증 통과**")
                    $candSuccess = $true
                    $candWorkingUrl = $url
                    $candItemCount = $xmlResult.itemCount

                    # 첫 5개 아이템 제목 샘플
                    try {
                        [xml]$x = $resp.Content
                        $titles = $x.SelectNodes('//item/title') | Select-Object -First 5
                        if ($titles.Count -eq 0) { $titles = $x.SelectNodes('//entry/title') | Select-Object -First 5 }
                        $null = $report.Add("")
                        $null = $report.Add("**최근 아이템 샘플 (최대 5개)**:")
                        foreach ($t in $titles) {
                            $title = $t.InnerText.Trim()
                            if ($title.Length -gt 150) { $title = $title.Substring(0, 147) + "..." }
                            $null = $report.Add("- $title")
                        }
                    } catch {
                        $null = $report.Add("- (아이템 샘플 추출 실패)")
                    }

                    $null = $report.Add("")
                    break
                }
            } else {
                # HTML인 경우 핵심 키워드 검색
                $html = $resp.Content
                $isHtml = $html -match '<html|<HTML'
                $null = $report.Add("- XML 파싱 실패. HTML 형식: $isHtml")
                if ($isHtml) {
                    $titleMatch = [regex]::Match($html, '<title[^>]*>(.*?)</title>', 'IgnoreCase')
                    if ($titleMatch.Success) {
                        $pageTitle = $titleMatch.Groups[1].Value.Trim()
                        $null = $report.Add("- 페이지 제목: ``$pageTitle``")
                    }
                    # 회수·공지·recall 키워드 카운트
                    $kwCount = @{
                        '회수' = ([regex]::Matches($html, '회수')).Count
                        '공지' = ([regex]::Matches($html, '공지')).Count
                        '판매중지' = ([regex]::Matches($html, '판매중지')).Count
                        'recall' = ([regex]::Matches($html, 'recall', 'IgnoreCase')).Count
                    }
                    $kwSummary = ($kwCount.GetEnumerator() | Where-Object { $_.Value -gt 0 } | ForEach-Object { "$($_.Key):$($_.Value)" }) -join ', '
                    if ($kwSummary) { $null = $report.Add("- 키워드 출현: $kwSummary") }
                    $candNote = "HTML 페이지 — 스크래퍼 필요"
                }
            }
        } catch {
            $null = $report.Add("- ❌ 요청 실패: $($_.Exception.Message)")
        }
        $null = $report.Add("")
    }

    if ($candSuccess) {
        $statusEmoji = "✅"
        $resultText = "**$candWorkingUrl** ($candItemCount items)"
    } else {
        $statusEmoji = "⚠️"
        if (-not $candNote) { $candNote = "RSS 검증 실패 — 대체 수단 필요" }
        $resultText = $candNote
    }
    $null = $summary.Add([PSCustomObject]@{
        ID = $cand.id
        Tier = $cand.tier
        Country = $cand.country
        Status = $statusEmoji
        Result = $resultText
    })

    $null = $report.Add("---")
    $null = $report.Add("")
}

# 요약 표 (보고서 상단에 삽입)
$summaryLines = @()
$summaryLines += "## 요약"
$summaryLines += ""
$summaryLines += "| ID | Tier | 국가 | 결과 | 비고 |"
$summaryLines += "|----|------|------|------|------|"
foreach ($s in $summary) {
    $summaryLines += "| $($s.ID) | $($s.Tier) | $($s.Country) | $($s.Status) | $($s.Result) |"
}
$summaryLines += ""

# 보고서 상단 4줄(헤더) 다음에 요약 삽입
$header = $report[0..3]
$body = $report[4..($report.Count - 1)]
$final = @($header) + @("") + $summaryLines + @($body)

$final | Out-File -FilePath $OutputFile -Encoding utf8
$final | Out-File -FilePath $LatestFile -Encoding utf8

Write-Output ""
Write-Output "=== 검증 완료 ==="
Write-Output "출력: $OutputFile"
Write-Output ""
Write-Output "요약:"
$summary | Format-Table -AutoSize
