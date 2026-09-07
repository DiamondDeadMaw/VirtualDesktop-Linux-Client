#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "encoder.h"

namespace py = pybind11;

PYBIND11_MODULE(native_encoder, m) {
    m.doc() = "Native X11 capture + vaPutImage + h264_vaapi encoder, replacing "
              "ffmpeg's hwupload (CPU-bound tiling) with vaPutImage (GPU-offloaded) "
              "for the real-X11-desktop capture case.";

    py::class_<NativeEncoder>(m, "NativeEncoder")
        .def(py::init<const std::string &, std::tuple<int, int, int, int>, int, int, int,
                      const std::string &, const std::optional<std::string> &>(),
             py::arg("display"), py::arg("region"), py::arg("out_width"), py::arg("out_height"),
             py::arg("fps"), py::arg("vaapi_device"), py::arg("xauthority") = py::none(),
             py::call_guard<py::gil_scoped_release>())
        .def("next_packet", [](NativeEncoder &self) {
            NativeEncoder::Packet pkt;
            {
                py::gil_scoped_release release;
                pkt = self.next_packet();
            }
            return py::make_tuple(
                py::bytes(reinterpret_cast<const char *>(pkt.data.data()), pkt.data.size()),
                pkt.is_key);
        })
        .def("stop", &NativeEncoder::stop, py::call_guard<py::gil_scoped_release>());
}
