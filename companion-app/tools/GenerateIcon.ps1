param([Parameter(Mandatory = $true)][string]$OutputPath)

Add-Type -AssemblyName System.Drawing

$sizes = @(16, 20, 24, 32, 40, 48, 64, 128, 256)
$images = @()
foreach ($size in $sizes) {
    $bitmap = [Drawing.Bitmap]::new($size, $size, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([Drawing.Color]::Transparent)

    $background = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(255, 21, 22, 24))
    $radius = [Math]::Max(3, [int]($size * 0.2))
    $path = [Drawing.Drawing2D.GraphicsPath]::new()
    $diameter = $radius * 2
    $path.AddArc(0, 0, $diameter, $diameter, 180, 90)
    $path.AddArc($size - $diameter - 1, 0, $diameter, $diameter, 270, 90)
    $path.AddArc($size - $diameter - 1, $size - $diameter - 1, $diameter, $diameter, 0, 90)
    $path.AddArc(0, $size - $diameter - 1, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    $graphics.FillPath($background, $path)

    $stroke = [Math]::Max(1.5, $size * 0.078)
    $pen = [Drawing.Pen]::new([Drawing.Color]::FromArgb(255, 140, 169, 214), $stroke)
    $pen.StartCap = [Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [Drawing.Drawing2D.LineJoin]::Round
    $curve = [Drawing.Drawing2D.GraphicsPath]::new()
    $curve.StartFigure()
    $curve.AddBezier($size * 0.16, $size * 0.70, $size * 0.28, $size * 0.22, $size * 0.68, $size * 0.13, $size * 0.84, $size * 0.52)
    $curve.AddBezier($size * 0.84, $size * 0.52, $size * 0.69, $size * 0.36, $size * 0.48, $size * 0.38, $size * 0.34, $size * 0.70)
    $graphics.DrawPath($pen, $curve)
    $graphics.DrawLine($pen, $size * 0.33, $size * 0.77, $size * 0.80, $size * 0.77)

    $memory = [IO.MemoryStream]::new()
    $bitmap.Save($memory, [Drawing.Imaging.ImageFormat]::Png)
    $images += ,$memory.ToArray()
    $curve.Dispose(); $pen.Dispose(); $path.Dispose(); $background.Dispose(); $graphics.Dispose(); $bitmap.Dispose(); $memory.Dispose()
}

$stream = [IO.File]::Create($OutputPath)
$writer = [IO.BinaryWriter]::new($stream)
$writer.Write([UInt16]0); $writer.Write([UInt16]1); $writer.Write([UInt16]$images.Count)
$offset = 6 + (16 * $images.Count)
for ($index = 0; $index -lt $images.Count; $index++) {
    $size = $sizes[$index]
    $writer.Write([byte]($(if ($size -eq 256) { 0 } else { $size })))
    $writer.Write([byte]($(if ($size -eq 256) { 0 } else { $size })))
    $writer.Write([byte]0); $writer.Write([byte]0)
    $writer.Write([UInt16]1); $writer.Write([UInt16]32)
    $writer.Write([UInt32]$images[$index].Length); $writer.Write([UInt32]$offset)
    $offset += $images[$index].Length
}
foreach ($image in $images) { $writer.Write($image) }
$writer.Dispose(); $stream.Dispose()
