# 用系统标准音频路径循环播放 WAV（最接近 Teams 渲染方式），供 loopback 捕获测试。
param([string]$wav = "D:\data\dsh_subtitle\16k_english.wav")
$p = New-Object System.Media.SoundPlayer $wav
$p.PlayLooping()
Start-Sleep -Seconds 25
$p.Stop()
Write-Output "played-loop-done"
