/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * Copied from facebook/meta-wearables-dat-ios samples/CameraAccess
 * (Media/VideoCaptureHandler.swift) — required by VideoFrameDecoder.
 */

import CoreMedia

extension CMSampleBuffer {
  /// Returns true if any NAL unit in the buffer is an HEVC keyframe type.
  /// Keyframe NAL types: BLA (16-18), IDR (19-20), CRA (21).
  func isHEVCKeyframe() -> Bool {
    guard let dataBuffer = CMSampleBufferGetDataBuffer(self) else { return false }
    let totalLength = CMBlockBufferGetDataLength(dataBuffer)
    var offset = 0

    // NAL units are in HVCC format: 4-byte big-endian length prefix followed by NAL data.
    while offset + 4 < totalLength {
      var nalLengthBE: UInt32 = 0
      guard CMBlockBufferCopyDataBytes(dataBuffer, atOffset: offset, dataLength: 4, destination: &nalLengthBE) == kCMBlockBufferNoErr
      else { break }
      let nalLength = Int(UInt32(bigEndian: nalLengthBE))
      offset += 4

      guard nalLength > 0, offset + nalLength <= totalLength else { break }

      // HEVC NAL header byte 0: forbidden_zero_bit(1) | nal_unit_type(6) | nuh_layer_id MSB(1)
      var nalHeader: UInt8 = 0
      guard CMBlockBufferCopyDataBytes(dataBuffer, atOffset: offset, dataLength: 1, destination: &nalHeader) == kCMBlockBufferNoErr
      else { break }
      let nalUnitType = (nalHeader >> 1) & 0x3F

      if nalUnitType >= 16 && nalUnitType <= 21 {
        return true
      }

      offset += nalLength
    }

    return false
  }
}
