catch {load vtktcl}
# Created oriented text
source ../../examplesTcl/vtkInt.tcl

# pipeline

vtkTextSource text1Source
    text1Source SetText "Text Source with Scalars"

vtkPolyDataMapper text1Mapper
    text1Mapper SetInput [text1Source GetOutput]

vtkActor text1Actor
    text1Actor SetMapper text1Mapper
    text1Actor SetScale .1 .1 .1

vtkTextSource text2Source
    text2Source SetText "Text Source without Scalars"
    text2Source BackingOff

vtkPolyDataMapper text2Mapper
    text2Mapper SetInput [text2Source GetOutput]
    text2Mapper ScalarVisibilityOff

vtkActor text2Actor
    text2Actor SetMapper text2Mapper
    [text2Actor GetProperty] SetColor 1 1 0
    text2Actor SetScale .1 .1 .1
    text2Actor AddPosition 0 -2 0

vtkVectorText text3Source
    text3Source SetText "Vector Text"

vtkPolyDataMapper text3Mapper
    text3Mapper SetInput [text3Source GetOutput]
    text3Mapper ScalarVisibilityOff

vtkActor text3Actor
    text3Actor SetMapper text3Mapper
    [text3Actor GetProperty] SetColor .1 1 0
    text3Actor AddPosition 0 -4 0

# create graphics stuff
vtkRenderer ren1
vtkRenderWindow renWin
    renWin AddRenderer ren1
    renWin SetSize 500 500

vtkRenderWindowInteractor iren
    iren SetRenderWindow renWin

ren1 AddActor text1Actor
ren1 AddActor text2Actor
ren1 AddActor text3Actor
[ren1 GetActiveCamera] Zoom 1.5
ren1 SetBackground .1 .2 .4

renWin Render

iren SetUserMethod {wm deiconify .vtkInteract}
iren Initialize

#renWin SetFileName "text.tcl.ppm"
#renWin SaveImageAsPPM

wm withdraw .
