@Entry
@Component
struct Index {
  build() {
    Column({ space: 12 }) {
      Text("__APP_NAME__")
        .fontSize(28)
        .fontWeight(FontWeight.Bold)
      Text('ArkUI migration scaffold')
        .fontSize(16)
        .fontColor('#666666')
    }
    .id('MigrationScaffold')
    .justifyContent(FlexAlign.Center)
    .alignItems(HorizontalAlign.Center)
    .width('100%')
    .height('100%')
  }
}
