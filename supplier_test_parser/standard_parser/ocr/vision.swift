import Foundation
import Vision
import PDFKit
import AppKit
// Usage: swift vision.swift input.pdf output-directory
let source=URL(fileURLWithPath:CommandLine.arguments[1])
let target=URL(fileURLWithPath:CommandLine.arguments[2])
guard let doc=PDFDocument(url:source) else { fatalError("Cannot open PDF") }
try FileManager.default.createDirectory(at:target,withIntermediateDirectories:true)
for i in 0..<doc.pageCount {
 try autoreleasepool {
  guard let page=doc.page(at:i) else {throw NSError(domain:"OCR",code:1)}
  let bounds=page.bounds(for:.mediaBox)
  let image=page.thumbnail(of:NSSize(width:bounds.width*2.5,height:bounds.height*2.5),for:.mediaBox)
  var rect=CGRect(origin:.zero,size:image.size)
  guard let cg=image.cgImage(forProposedRect:&rect,context:nil,hints:nil) else {throw NSError(domain:"OCR",code:2)}
  let request=VNRecognizeTextRequest()
  request.recognitionLevel = .accurate
  request.recognitionLanguages=["zh-Hans","en-US"]
  request.usesLanguageCorrection=false
  try VNImageRequestHandler(cgImage:cg).perform([request])
  let blocks=(request.results ?? []).compactMap { ob -> [String:Any]? in
   guard let c=ob.topCandidates(1).first else{return nil}
   return ["text":c.string,"score":c.confidence,"bbox":[ob.boundingBox.minX,ob.boundingBox.minY,ob.boundingBox.width,ob.boundingBox.height]]
  }
  let result:[String:Any]=["file":source.lastPathComponent,"page":i+1,"method":"apple_vision_local","text":blocks.map{$0["text"] as! String}.joined(separator:"\n"),"blocks":blocks]
  try JSONSerialization.data(withJSONObject:result,options:[.sortedKeys]).write(to:target.appendingPathComponent(String(format:"%03d.json",i+1)),options:.atomic)
  print("Page \(i+1)/\(doc.pageCount)");fflush(stdout)
 }
}
